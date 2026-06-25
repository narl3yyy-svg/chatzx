#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export CHATXZ_ROOT="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

# Check for virtual env (:- avoids nounset error when VIRTUAL_ENV is unset)
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PIP="$VIRTUAL_ENV/bin/pip"
    PYTHON="$VIRTUAL_ENV/bin/python"
else
    PIP="pip"
    PYTHON="python3"
fi

install_deps() {
    echo "Installing dependencies..."
    $PIP install --user --break-system-packages rns aiohttp 2>/dev/null || \
    $PIP install rns aiohttp
}

case "${1:-}" in
    install)
        install_deps
        $PIP install --user --break-system-packages -e . 2>/dev/null || $PIP install -e .
        echo "Done. Run ./run.sh web"
        ;;
    web|server)
        install_deps
        chmod +x "$DIR/scripts/launch-server.sh" 2>/dev/null || true
        PYTHON="$PYTHON" CHATXZ_ROOT="$DIR" "$DIR/scripts/launch-server.sh" "${@:2}"
        ;;
    cli)
        install_deps
        $PYTHON -m chatxz.app "${@:2}"
        ;;
    *)
        echo "chatxz - Reticulum Chat"
        echo
        echo "Usage: ./run.sh <command> [args]"
        echo
        echo "Commands:"
        echo "  install          Install dependencies and package (stays in this folder)"
        echo "  web [--share] [--verbose] [--debug] [--force]  Start web server"
        echo "  cli [options]    Start CLI mode"
        echo
        echo "Also: ./install.sh (system install)  ./uninstall.sh (remove app data)"
        echo
        echo "Examples:"
        echo "  ./run.sh web"
        echo "  ./run.sh web --share    # Linux / macOS LAN access"
        echo "  ./run.sh cli --daemon"
        echo
        echo "macOS portable: see GitHub Releases (.dmg) or: bash scripts/install-macos.sh"
        echo "  ./run.sh cli --connect <hash> --send hello"
        ;;
esac
