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
用法: $(basename "$0") [命令|选项]

启动:
  （无参数）              前台启动 Mihomo
  --background | -b       后台启动（日志写入 cache/mihomo.log）
  --update-geo [选项]     更新 conf/geo（见 update-geo.sh --help）

交互管理（推荐）:
  menu | ctl              进入交互菜单（订阅 / 节点 / 测速 / IP / 连通性）

非交互 CLI（脚本用，支持 --json）:
  ip | verify-all | connectivity | status | sub … | nodes | select | delay-batch …
  完整帮助: $(basename "$0") ctl --help

  -h, --help              显示此帮助
EOF
}

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

_prepare_runtime() {
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
}

_mihomo_project_root() {
  if [[ -n "${MIHOMO_PROJECT_ROOT:-}" ]]; then
    echo "$MIHOMO_PROJECT_ROOT"
    return
  fi
  if [[ -d "$ROOT/../../platform/backend" ]]; then
    cd "$ROOT/../.." && pwd
  else
    echo "$ROOT"
  fi
}

_resolve_python() {
  if [[ -n "${MIHOMO_PYTHON:-}" && -x "$MIHOMO_PYTHON" ]]; then
    echo "$MIHOMO_PYTHON"
    return
  fi
  local candidate
  for candidate in \
    "$ROOT/.venv/bin/python" \
    "$ROOT/../../platform/backend/.venv/bin/python" \
    "$ROOT/admin/.venv/bin/python"; do
    if [[ -x "$candidate" ]] && "$candidate" -c "import yaml" 2>/dev/null; then
      echo "$candidate"
      return
    fi
  done
  if command -v python3 >/dev/null 2>&1 && python3 -c "import yaml" 2>/dev/null; then
    echo python3
    return
  fi
  echo "CLI 需要 PyYAML：$ROOT/.venv 执行 pip install -r python/requirements.txt，或设置 MIHOMO_PYTHON。" >&2
  exit 1
}

_run_cli() {
  export MIHOMO_DIR="$ROOT"
  export MIHOMO_PROJECT_ROOT="$(_mihomo_project_root)"
  export MIHOMO_STANDALONE=1
  export PYTHONPATH="$ROOT/python${PYTHONPATH:+:$PYTHONPATH}"
  exec "$(_resolve_python)" -m mihomo_mgr "$@"
}

case "${1:-}" in
  --update-geo)
    shift
    exec "$ROOT/update-geo.sh" "$@"
    ;;
  --background|-b)
    _prepare_runtime
    log_file="$CACHE_DIR/mihomo.log"
    mkdir -p "$CACHE_DIR"
    nohup "$BIN" -d "$CACHE_DIR" -f "$CONFIG" >>"$log_file" 2>&1 &
    echo "Mihomo 已在后台启动 (PID $!)，日志: $log_file"
    exit 0
    ;;
  menu|interactive|i)
    _run_cli
    ;;
  ctl)
    shift
    if [[ $# -eq 0 ]]; then
      _run_cli
    else
      _run_cli "$@"
    fi
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  status|sub|nodes|select|delay|delay-batch|ip|verify-all|connectivity)
    _run_cli "$@"
    ;;
  "")
    _prepare_runtime
    exec "$BIN" -d "$CACHE_DIR" -f "$CONFIG"
    ;;
  *)
    echo "未知参数: $1" >&2
    usage >&2
    exit 1
    ;;
esac
