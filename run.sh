#!/usr/bin/env bash
# 本地启动 Mihomo（cache/active.yaml 为运行配置，目录：bin / conf / cache）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$ROOT/bin"
CONF_DIR="$ROOT/conf"
CACHE_DIR="$ROOT/cache"
GEO_DIR="$CONF_DIR/geo"
BIN="$BIN_DIR/mihomo"
CONFIG="${MIHOMO_CONFIG:-$CACHE_DIR/active.yaml}"

usage() {
  cat <<EOF
用法: $(basename "$0") [选项]

  （无参数）启动 Mihomo
  --update-geo [update-geo 选项]  更新 conf/geo 规则库（见 update-geo.sh --help）
  -h, --help                        显示此帮助
EOF
}

case "${1:-}" in
  --update-geo)
    shift
    exec "$ROOT/update-geo.sh" "$@"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    echo "未知参数: $1" >&2
    usage >&2
    exit 1
    ;;
esac

migrate_legacy() {
  mkdir -p "$BIN_DIR" "$CONF_DIR/profiles" "$GEO_DIR" "$CACHE_DIR"
  if [[ -f "$ROOT/mihomo" && ! -f "$BIN" ]]; then
    mv "$ROOT/mihomo" "$BIN"
    chmod +x "$BIN"
  fi
  for gz in "$ROOT"/mihomo-*.gz; do
    [[ -f "$gz" ]] || continue
    [[ -f "$BIN_DIR/$(basename "$gz")" ]] || mv "$gz" "$BIN_DIR/"
  done
  if [[ -f "$ROOT/iKuuu_V2.yaml" && ! -f "$CONFIG" ]]; then
    mv "$ROOT/iKuuu_V2.yaml" "$CONFIG"
  fi
  if [[ -d "$ROOT/profiles" ]]; then
    for f in "$ROOT/profiles"/*; do
      [[ -e "$f" ]] || continue
      [[ -e "$CONF_DIR/profiles/$(basename "$f")" ]] || mv "$f" "$CONF_DIR/profiles/"
    done
    rmdir "$ROOT/profiles" 2>/dev/null || true
  fi
  for geo in GeoSite.dat GeoIP.dat country.mmdb geoip.metadb; do
    if [[ -f "$ROOT/$geo" && ! -f "$GEO_DIR/$geo" ]]; then
      mv "$ROOT/$geo" "$GEO_DIR/$geo"
    fi
  done
  for name in cache.db mihomo.log; do
    if [[ -f "$ROOT/$name" && ! -f "$CACHE_DIR/$name" ]]; then
      mv "$ROOT/$name" "$CACHE_DIR/$name"
    fi
  done
  if [[ -f "$ROOT/.subscription.lock" && ! -f "$CACHE_DIR/.subscription.lock" ]]; then
    mv "$ROOT/.subscription.lock" "$CACHE_DIR/.subscription.lock"
  fi
  for bak in "$ROOT"/iKuuu_V2.yaml.*.bak; do
    [[ -f "$bak" ]] || continue
    dest="$CONF_DIR/active.yaml${bak#"$ROOT/iKuuu_V2.yaml"}"
    [[ -f "$dest" ]] || mv "$bak" "$dest"
  done
}

link_geo() {
  for geo in GeoSite.dat GeoIP.dat country.mmdb geoip.metadb; do
    if [[ -f "$GEO_DIR/$geo" ]]; then
      ln -sf "$GEO_DIR/$geo" "$CACHE_DIR/$geo"
    fi
  done
}

migrate_legacy
if [[ -f "$CONF_DIR/active.yaml" && ( ! -f "$CONFIG" || "$CONF_DIR/active.yaml" -nt "$CONFIG" ) ]]; then
  cp -a "$CONF_DIR/active.yaml" "$CONFIG"
fi
link_geo

if [[ ! -x "$BIN" ]]; then
  echo "缺少 $BIN，请先解压 mihomo 二进制到 bin/" >&2
  exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
  if [[ -f "$ROOT/iKuuu_V2.yaml" ]]; then
    CONFIG="$ROOT/iKuuu_V2.yaml"
  else
    echo "缺少配置: $CONFIG" >&2
    exit 1
  fi
fi
exec "$BIN" -d "$CACHE_DIR" -f "$CONFIG"
