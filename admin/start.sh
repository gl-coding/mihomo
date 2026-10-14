#!/usr/bin/env bash
# Mihomo 代理管理页：单独运行（不依赖 nginxRouter 平台）
set -euo pipefail

ADMIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$ADMIN_DIR/.." && pwd)"
VENV="$ADMIN_DIR/.venv"
DIST="$ADMIN_DIR/standalone/dist"
PYTHON="${MIHOMO_ADMIN_PYTHON:-python3}"
HOST="${MIHOMO_ADMIN_HOST:-127.0.0.1}"
PORT="${MIHOMO_ADMIN_PORT:-8765}"
NPM_CACHE="${MIHOMO_ADMIN_NPM_CACHE:-$ADMIN_DIR/.cache/npm}"

usage() {
  cat <<EOF
用法: $(basename "$0") [选项]

  构建管理页前端并启动 Django API + 静态页面（默认仅本机）。

选项:
  --host HOST     监听地址（默认 127.0.0.1）
  --port PORT     监听端口（默认 8765）
  --skip-build    跳过 npm build（需已有 standalone/dist）
  -h, --help      显示此帮助

环境变量:
  MIHOMO_ADMIN_HOST / MIHOMO_ADMIN_PORT
  MIHOMO_API_BASE / MIHOMO_HTTP_PROXY（与 nginxRouter 相同）

访问: http://127.0.0.1:8765/
订阅设置文件: $REPO_ROOT/env/.env.mihomo.local（模板 env/.env.example）
EOF
}

SKIP_BUILD=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "未找到 Python 3，请安装或设置 MIHOMO_ADMIN_PYTHON。" >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  echo "创建虚拟环境…"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck source=/dev/null
source "$VENV/bin/activate"
pip install -q -r "$ADMIN_DIR/requirements.txt"

NEEDS_BUILD=false
if [[ "$SKIP_BUILD" == false ]]; then
  if [[ ! -f "$DIST/index.html" ]]; then
    NEEDS_BUILD=true
  elif [[ "$ADMIN_DIR/package.json" -nt "$DIST/index.html" ]]; then
    NEEDS_BUILD=true
  elif [[ -n "$(find "$ADMIN_DIR/standalone/src" -type f -newer "$DIST/index.html" -print -quit 2>/dev/null)" ]]; then
    NEEDS_BUILD=true
  fi
fi

if [[ "$NEEDS_BUILD" == true ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "构建管理页需要 Node.js 与 npm。" >&2
    exit 1
  fi
  cd "$ADMIN_DIR"
  if [[ ! -x node_modules/.bin/vite ]]; then
    echo "安装前端依赖…"
    npm install --no-audit --no-fund --cache "$NPM_CACHE"
  fi
  echo "构建管理页…"
  npm run build
fi

cd "$ADMIN_DIR"
export MIHOMO_STANDALONE=1
echo "Mihomo 管理页: http://${HOST}:${PORT}/"
exec python manage.py runserver "${HOST}:${PORT}"
