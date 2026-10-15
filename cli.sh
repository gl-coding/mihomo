#!/usr/bin/env bash
# 可安装到 PATH 的管理入口；解析符号链接后从任意目录运行。
set -euo pipefail
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  SOURCE_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" == /* ]] || SOURCE="$SOURCE_DIR/$SOURCE"
done
ROOT="$(cd -P "$(dirname "$SOURCE")" && pwd)"
if [[ $# -eq 0 ]]; then
  exec "$ROOT/run.sh" tui
fi
exec "$ROOT/run.sh" ctl "$@"
