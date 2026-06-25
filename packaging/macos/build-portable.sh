#!/usr/bin/env bash
# Build chatxz.app and a release .zip (run on macOS).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

echo "Installing build dependencies..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install "rns>=1.3.0" "aiohttp>=3.9.0" pyinstaller
"$PYTHON" -m pip install -e .

if [ -z "${VERSION:-}" ]; then
  VERSION="$("$PYTHON" -c "from chatxz._version import __version__; print(__version__)")"
fi
echo "Building chatxz v${VERSION} for macOS..."

pyinstaller --noconfirm packaging/macos/chatxz-portable.spec

if [ ! -d "dist/chatxz.app" ]; then
    echo "ERROR: dist/chatxz.app not found"
    exit 1
fi

cp packaging/macos/README-PORTABLE.txt dist/README-PORTABLE.txt

ZIP="chatxz-${VERSION}-macos.zip"
rm -f "dist/${ZIP}"
ditto -c -k --keepParent dist/chatxz.app "dist/${ZIP}"

echo "Built:"
echo "  dist/chatxz.app"
echo "  dist/${ZIP}"