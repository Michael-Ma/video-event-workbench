#!/usr/bin/env bash
# Bootstrap project dependencies, validate configuration, start all services.
set -euo pipefail
VEW_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$VEW_ROOT"
for arg in "$@"; do
  case "$arg" in
    --check|--require-api-key) ;;
    -h|--help)
      cat <<'HELP'
用法：./start.sh [--check] [--require-api-key]
默认：安装缺少的依赖，检查配置，启动 API、worker 和网页。
--check：安装依赖并检查，不启动服务。
--require-api-key：必须配置 GEMINI_API_KEY；默认允许内置示例。
停止服务：Ctrl+C。地址：http://127.0.0.1:5173
HELP
      exit 0 ;;
    *) echo "不支持的参数：$arg（查看 ./start.sh --help）" >&2; exit 2 ;;
  esac
done
VEW_OS="$(uname -s)"
case "$VEW_OS" in Darwin) VEW_PLATFORM=darwin ;; Linux) VEW_PLATFORM=linux ;;
  *) echo "此脚本支持 macOS 和 Linux。" >&2; exit 1 ;; esac
case "$(uname -m)" in arm64|aarch64) VEW_ARCH=arm64 ;; x86_64|amd64) VEW_ARCH=x64 ;;
  *) echo "此脚本支持 arm64 和 x64。" >&2; exit 1 ;; esac
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
VEW_NODE_VERSION=24.15.0
VEW_NODE_FOLDER="node-v${VEW_NODE_VERSION}-${VEW_PLATFORM}-${VEW_ARCH}"
export PATH="$VEW_ROOT/.tools/bin:$VEW_ROOT/.tools/$VEW_NODE_FOLDER/bin:$PATH"
VEW_TEMP="$(mktemp -d)"
trap 'rm -rf -- "$VEW_TEMP"' EXIT
download() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error --retry 2 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$1" -O "$2"
  else
    echo "需要 curl 或 wget 才能安装缺少的工具。" >&2; exit 1
  fi
}
if ! command -v uv >/dev/null 2>&1; then
  echo "[安装] uv → 项目 .tools/bin"
  download https://astral.sh/uv/0.10.3/install.sh "$VEW_TEMP/uv-install.sh"
  UV_UNMANAGED_INSTALL="$VEW_ROOT/.tools/bin" sh "$VEW_TEMP/uv-install.sh"
fi
echo "[依赖] 同步 Python 3.13 与锁定的后端依赖"
env -u GEMINI_API_KEY uv sync --locked --python 3.13 --extra dev
VEW_PYTHON="$VEW_ROOT/.venv/bin/python"
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1 ||
   ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
  echo "[安装] Node.js $VEW_NODE_VERSION → 项目 .tools"
  VEW_ARCHIVE="$VEW_NODE_FOLDER.tar.gz"
  VEW_NODE_BASE="https://nodejs.org/dist/v$VEW_NODE_VERSION"
  download "$VEW_NODE_BASE/SHASUMS256.txt" "$VEW_TEMP/SHASUMS256.txt"
  download "$VEW_NODE_BASE/$VEW_ARCHIVE" "$VEW_TEMP/$VEW_ARCHIVE"
  "$VEW_PYTHON" - "$VEW_TEMP/SHASUMS256.txt" "$VEW_TEMP/$VEW_ARCHIVE" <<'PY'
import hashlib
import sys
from pathlib import Path
manifest, archive = map(Path, sys.argv[1:])
checksums = dict(line.split(maxsplit=1)[::-1] for line in manifest.read_text().splitlines())
with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
if checksums.get(archive.name) != actual:
    raise SystemExit('Node.js 下载校验失败，未安装。')
PY
  mkdir -p "$VEW_ROOT/.tools"
  tar -xzf "$VEW_TEMP/$VEW_ARCHIVE" -C "$VEW_ROOT/.tools"
  hash -r
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "[安装] FFmpeg 与 FFprobe"
  if [ "$VEW_OS" = Darwin ]; then
    if ! command -v brew >/dev/null 2>&1; then
      echo "[安装] Homebrew（系统安装可能要求管理员密码）"
      download https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "$VEW_TEMP/brew-install.sh"
      /bin/bash "$VEW_TEMP/brew-install.sh"
      hash -r
    fi
    brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then
    if [ "$(id -u)" = 0 ]; then
      apt-get update
      apt-get install -y ffmpeg
    else
      sudo apt-get update
      sudo apt-get install -y ffmpeg
    fi
  elif command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  else
    echo "此 Linux 环境没有 apt-get 或 Homebrew，请先安装 ffmpeg。" >&2; exit 1
  fi
fi
VEW_LOCK_HASH="$("$VEW_PYTHON" - <<'PY'
import hashlib
from pathlib import Path
stamp = hashlib.sha256()
for name in ('package.json', 'package-lock.json'):
    stamp.update((Path('web') / name).read_bytes())
print(stamp.hexdigest())
PY
)"
VEW_STAMP="$VEW_ROOT/web/node_modules/.vew-dependencies.sha256"
if [ ! -x web/node_modules/.bin/vite ] || [ ! -x web/node_modules/.bin/tsc ] ||
   [ ! -f "$VEW_STAMP" ] || [ "$(cat "$VEW_STAMP")" != "$VEW_LOCK_HASH" ]; then
  echo "[依赖] 安装锁定的前端依赖"
  env -u GEMINI_API_KEY npm --prefix web ci --include=dev --no-audit --no-fund
  printf '%s\n' "$VEW_LOCK_HASH" > "$VEW_STAMP"
else
  echo "[依赖] 前端依赖已就绪"
fi
rm -rf -- "$VEW_TEMP"
trap - EXIT
export PYTHONPATH="$VEW_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$VEW_PYTHON" "$VEW_ROOT/scripts/dev.py" "$@"
