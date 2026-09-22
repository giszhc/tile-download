#!/usr/bin/env bash
# 底图瓦片下载 —— macOS / Linux 启动脚本
# 用法：bash run.sh   （或 ./run.sh）
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$DIR/tile-download"
PY="$DIR/tile_download.py"

# 优先用同目录下的独立可执行文件
if [ -f "$BIN" ]; then
  chmod +x "$BIN" 2>/dev/null || true
  # macOS：下载来的二进制带隔离标记，不去掉会被 Gatekeeper 直接杀掉
  if [ "$(uname)" = "Darwin" ]; then
    xattr -d com.apple.quarantine "$BIN" 2>/dev/null || true
  fi
  exec "$BIN"
fi

# 没有二进制就退回源码运行
if [ -f "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    exec python3 "$PY"
  elif command -v python >/dev/null 2>&1; then
    exec python "$PY"
  fi
fi

echo "没找到 tile-download 可执行文件，也没检测到 python3。" >&2
echo "请到 Releases 页面重新下载对应系统的压缩包。" >&2
exit 1
