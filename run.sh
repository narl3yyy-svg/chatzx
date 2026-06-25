#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export CHATXZ_ROOT="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

# Check for virtual env (:- avoids nounset error when VIRTUAL_ENV is unset)
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    PYTHON="python3"
fi

pip_cmd() {
    "$PYTHON" -m pip "$@"
}

install_deps() {
    if "$PYTHON" -c "import rns, aiohttp" 2>/dev/null; then
        return 0
    fi
    echo "Installing dependencies (first run only)..."
    if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
        echo "pip not found for $PYTHON"
        echo "Fix: $PYTHON -m ensurepip --upgrade"
        echo "Or run: bash scripts/install-macos.sh"
        exit 1
    fi
    pip_cmd install -q --user --break-system-packages rns aiohttp 2>/dev/null || \
    pip_cmd install -q --user rns aiohttp 2>/dev/null || \
    pip_cmd install -q rns aiohttp
}

case "${1:-}" in
    install)
        install_deps
        pip_cmd install --user --break-system-packages -e . 2>/dev/null || \
        pip_cmd install --user -e . 2>/dev/null || \
        pip_cmd install -e .
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
        echo "Windows (cmd):  run.bat web --share"
        echo "Windows (Git Bash):  ./run.sh web --share"
        echo "  ./run.sh cli --connect <hash> --send hello"
        ;;
esac
