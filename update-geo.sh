#!/usr/bin/env bash
# 从 MetaCubeX/meta-rules-dat 更新 conf/geo/（与订阅更新分离，适合 SSH/cron）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
GEO_DIR="$ROOT/conf/geo"
CACHE_DIR="$ROOT/cache"
BASE_URL="${MIHOMO_GEO_BASE:-https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest}"
TIMEOUT="${MIHOMO_GEO_TIMEOUT:-180}"
USE_PROXY="${MIHOMO_GEO_VIA_PROXY:-auto}"

usage() {
  cat <<EOF
用法: $(basename "$0") [选项]

  更新 GeoSite / GeoIP 规则库到 conf/geo/，并在 cache/ 下刷新符号链接。
  大文件走 GitHub，建议 Mihomo 已启动且本机 7890 可用（默认 auto 会探测）。

选项:
  --via-proxy     强制经 http://127.0.0.1:7890 下载
  --direct        直连，不用代理
  --only NAME     只更新一项: geosite | geoip-mmdb | geoip-metadb | all（默认 all）
  -h, --help      显示此帮助

示例:
  ./update-geo.sh
  MIHOMO_GEO_VIA_PROXY=true ./update-geo.sh --only geosite
EOF
}

proxy_env() {
  local proxy="${MIHOMO_HTTP_PROXY:-http://127.0.0.1:7890}"
  export http_proxy="$proxy" https_proxy="$proxy" HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy"
}

proxy_reachable() {
  curl -fsS -m 2 -o /dev/null -x "${MIHOMO_HTTP_PROXY:-http://127.0.0.1:7890}" "https://github.com/" 2>/dev/null
}

resolve_proxy_mode() {
  case "$USE_PROXY" in
    1|true|yes|on) echo proxy ;;
    0|false|no|off) echo direct ;;
    auto|*)
      if proxy_reachable; then echo proxy; else echo direct; fi
      ;;
  esac
}

link_geo() {
  mkdir -p "$CACHE_DIR"
  for geo in GeoSite.dat GeoIP.dat country.mmdb geoip.metadb; do
    if [[ -f "$GEO_DIR/$geo" ]]; then
      ln -sf "$GEO_DIR/$geo" "$CACHE_DIR/$geo"
    fi
  done
}

fetch_one() {
  local url="$1" dest="$2" label="$3"
  local mode="$4"
  local tmp
  tmp="$(mktemp "$GEO_DIR/.download.XXXXXX")"
  echo "→ $label ($mode)"
  if [[ "$mode" == proxy ]]; then
    proxy_env
    curl -fL --retry 2 -m "$TIMEOUT" -o "$tmp" "$url"
  else
    env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
      curl -fL --retry 2 -m "$TIMEOUT" -o "$tmp" "$url"
  fi
  chmod 644 "$tmp"
  mv -f "$tmp" "$dest"
  echo "  ✓ $(basename "$dest") ($(wc -c < "$dest") bytes)"
}

ONLY=all
while [[ $# -gt 0 ]]; do
  case "$1" in
    --via-proxy) USE_PROXY=true; shift ;;
    --direct) USE_PROXY=false; shift ;;
    --only) ONLY="${2:?缺少 --only 参数}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 1 ;;
  esac
done

mkdir -p "$GEO_DIR" "$CACHE_DIR"
MODE="$(resolve_proxy_mode)"
echo "Geo 更新（$MODE）→ $GEO_DIR"

want() {
  [[ "$ONLY" == all || "$ONLY" == "$1" ]]
}

if want geosite; then
  fetch_one "$BASE_URL/geosite.dat" "$GEO_DIR/GeoSite.dat" "GeoSite" "$MODE"
fi
if want geoip-mmdb; then
  fetch_one "$BASE_URL/country.mmdb" "$GEO_DIR/country.mmdb" "country.mmdb" "$MODE"
fi
if want geoip-metadb; then
  fetch_one "$BASE_URL/geoip.metadb" "$GEO_DIR/geoip.metadb" "geoip.metadb" "$MODE"
fi

link_geo
echo "完成。若 Mihomo 已在运行，一般无需重启；校验/规则会使用新文件。"
