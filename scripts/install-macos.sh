#!/usr/bin/env bash
# macOS install — same workflow as Arch: ./run.sh web --share
set -e

echo "chatxz - macOS Installer"
echo "========================"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This script is for macOS only."
    exit 1
fi

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required."
    if command -v brew >/dev/null 2>&1; then
        echo "Install with: brew install python3"
    else
        echo "Install from https://www.python.org/downloads/macos/"
    fi
    exit 1
fi

PYTHON="$(command -v python3)"
echo "Using Python: $($PYTHON --version 2>&1) at $PYTHON"

read -p "Install Opus for Rust media engine? [Y/n]: " opus_opt
if [[ ! "$opus_opt" =~ ^[Nn]$ ]]; then
    if command -v brew >/dev/null 2>&1; then
        brew install opus || true
    fi
fi

echo "Installing chatxz..."
"$PYTHON" -m pip install --user --upgrade pip
"$PYTHON" -m pip install --user "rns>=1.3.0" "aiohttp>=3.9.0"
if [ -n "$EXTRA" ]; then
    "$PYTHON" -m pip install --user -e ".${EXTRA}" 2>/dev/null || "$PYTHON" -m pip install --user -e .
else
    "$PYTHON" -m pip install --user -e .
fi

chmod +x "$DIR/run.sh" "$DIR/scripts/launch-server.sh" 2>/dev/null || true

echo ""
echo "chatxz installed!"
echo ""
echo "Start the web UI (LAN accessible):"
echo "  cd $DIR"
echo "  ./run.sh web --share"
echo ""
echo "Open http://localhost:8742 in your browser."
