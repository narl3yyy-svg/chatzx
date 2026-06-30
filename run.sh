#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
export CHATXZ_ROOT="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
export PIP_DISABLE_PIP_VERSION_CHECK=1

VENV="$DIR/.venv"
VENV_PY="$VENV/bin/python"
READY_MARK="$VENV/.ready"
PYTHON=""

system_python() {
    command -v python3 2>/dev/null || command -v python 2>/dev/null || true
}

resolve_python() {
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        PYTHON="$VIRTUAL_ENV/bin/python"
    elif [ -x "$VENV_PY" ]; then
        PYTHON="$VENV_PY"
    else
        PYTHON="$(system_python)"
        PYTHON="${PYTHON:-python3}"
    fi
}

deps_core_ok() {
    "$1" -c "import RNS, aiohttp" 2>/dev/null
}

ensure_venv() {
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        resolve_python
        if ! deps_core_ok "$PYTHON"; then
            echo "Installing dependencies in active virtualenv..."
            "$PYTHON" -m pip install -q "rns>=1.3.0" "aiohttp>=3.9.0"
        fi
        return 0
    fi

    if [ -x "$VENV_PY" ] && deps_core_ok "$VENV_PY"; then
        PYTHON="$VENV_PY"
        touch "$READY_MARK"
        return 0
    fi

    local SYS_PY
    SYS_PY="$(system_python)"
    if [ -z "$SYS_PY" ] || ! "$SYS_PY" --version >/dev/null 2>&1; then
        echo "Python 3 not found."
        echo "Ubuntu: sudo apt install python3 python3-venv python3-pip"
        exit 1
    fi

    echo "First run: setting up dependencies in .venv ..."
    if [ -d "$VENV" ]; then
        rm -rf "$VENV"
    fi
    if [ "$(uname -s 2>/dev/null || echo)" = "Linux" ]; then
        if ! "$SYS_PY" -m venv --system-site-packages "$VENV"; then
            VENV_CREATE_FAILED=1
        fi
    elif ! "$SYS_PY" -m venv "$VENV"; then
        VENV_CREATE_FAILED=1
    fi
    if [ "${VENV_CREATE_FAILED:-}" = "1" ]; then
        echo "Failed to create .venv."
        echo "Ubuntu/Debian: sudo apt install python3-venv python3-pip"
        exit 1
    fi

    PYTHON="$VENV_PY"
    "$PYTHON" -m pip install -q --upgrade pip
    if ! "$PYTHON" -m pip install -q "rns>=1.3.0" "aiohttp>=3.9.0"; then
        echo "Failed to install rns/aiohttp in .venv"
        exit 1
    fi
    touch "$READY_MARK"
}

install_deps() {
    ensure_venv
}

case "${1:-}" in
    install)
        install_deps
        "$PYTHON" -m pip install -e .
        if command -v cargo >/dev/null 2>&1; then
            echo "[rust] Building chatxz-server..."
            cargo build --release -p chatxz-server
        else
            echo "[rust] Install Rust (https://rustup.rs) to build the media server."
        fi
        echo "Done. Run ./run.sh web"
        ;;
    web|server)
        install_deps
        chmod +x "$DIR/scripts/launch-server.sh" 2>/dev/null || true
        PYTHON="$PYTHON" CHATXZ_ROOT="$DIR" "$DIR/scripts/launch-server.sh" "${@:2}"
        ;;
    cli)
        install_deps
        "$PYTHON" -m chatxz.app "${@:2}"
        ;;
    *)
        echo "chatxz v2 — Reticulum Chat (Rust media + Python RNS)"
        echo
        echo "Usage: ./run.sh <command> [args]"
        echo
        echo "Commands:"
        echo "  install          Install Python deps, Rust server, and package"
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
        ;;
esac