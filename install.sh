#!/usr/bin/env bash
# chatxz installer — run from the cloned/downloaded repo folder.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "chatxz installer"
echo "================"
echo "Repository: $DIR"
echo

if [ -f /etc/arch-release ]; then
    exec bash "$DIR/scripts/install-arch.sh"
fi

if [ -f /etc/debian_version ]; then
    if grep -qi ubuntu /etc/os-release 2>/dev/null; then
        exec bash "$DIR/scripts/install-ubuntu.sh"
    fi
    exec bash "$DIR/scripts/install-ubuntu.sh"
fi

if [ "$(uname -s)" = "Darwin" ]; then
    exec bash "$DIR/scripts/install-macos.sh"
fi

echo "No distro-specific installer found."
echo "Installing Python dependencies into this checkout..."
exec bash "$DIR/run.sh" install