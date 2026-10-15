#!/usr/bin/env bash
# 将管理 CLI 链接到用户命令目录，不覆盖已有命令。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf '用法：%s [--bin-dir DIR]\n默认安装到 ~/.local/bin/mihomo\n' "$0"
  exit 0
fi
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--bin-dir" || -z "$2" ]]; then
    printf '用法：%s [--bin-dir DIR]\n' "$0" >&2
    exit 2
  fi
  BIN_DIR="$2"
fi
mkdir -p "$BIN_DIR"
BIN_DIR="$(cd "$BIN_DIR" && pwd)"
TARGET="$BIN_DIR/mihomo"
if [[ -L "$TARGET" && "$(readlink "$TARGET")" == "$ROOT/cli.sh" ]]; then
  printf 'CLI 已安装：%s\n' "$TARGET"
elif [[ -e "$TARGET" || -L "$TARGET" ]]; then
  printf '目标已存在，未覆盖：%s\n请使用 --bin-dir 选择其他目录。\n' "$TARGET" >&2
  exit 1
else
  ln -s "$ROOT/cli.sh" "$TARGET"
  printf 'CLI 已安装：%s\n' "$TARGET"
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf '请将目录加入 PATH：%s\n' "$BIN_DIR" ;;
esac
printf '用法：mihomo tui / mihomo status / mihomo --help\n'
