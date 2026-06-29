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

deps_voice_ok() {
    "$1" -c "from chatxz.core.audio import call_audio_available; import sys; sys.exit(0 if call_audio_available() else 1)" 2>/dev/null
}

recreate_venv_with_system_packages() {
    local SYS_PY
    SYS_PY="$(system_python)"
    if [ -z "$SYS_PY" ]; then
        return 1
    fi
    echo "[setup] Recreating .venv with --system-site-packages (apt python3-pyaudio)..."
    rm -rf "$VENV"
    if ! "$SYS_PY" -m venv --system-site-packages "$VENV"; then
        return 1
    fi
    "$VENV_PY" -m pip install -q --upgrade pip
    if ! "$VENV_PY" -m pip install -q "rns>=1.3.0" "aiohttp>=3.9.0"; then
        return 1
    fi

    touch "$READY_MARK"
    PYTHON="$VENV_PY"
    return 0
}

install_voice_deps() {
    local py="$1"
    if deps_voice_ok "$py"; then
        return 0
    fi
    local SYS_PY
    SYS_PY="$(system_python)"
    if [ "$py" = "$VENV_PY" ] && [ -n "$SYS_PY" ] \
        && "$SYS_PY" -c "import pyaudio" 2>/dev/null \
        && ! "$py" -c "import pyaudio" 2>/dev/null; then
        if recreate_venv_with_system_packages && deps_voice_ok "$VENV_PY"; then
            echo "[setup] Using apt python3-pyaudio via --system-site-packages"
            return 0
        fi
    fi
    echo "Installing voice dependencies (pyaudio)..."
    local log="$DIR/.voice-install.log"
    if "$py" -m pip install -q pyaudio 2>"$log"; then
        rm -f "$log"
        return 0
    fi
    echo "[setup] Native call audio unavailable (libopus and/or pyaudio). Browser Opus fallback still works."
    if [ -s "$log" ]; then
        tail -n 2 "$log" | sed 's/^/[setup] /'
    fi
    echo "  Ubuntu/Debian: sudo apt install libopus0 portaudio19-dev python3-dev python3-pyaudio"
    echo "  Arch: sudo pacman -S opus portaudio python-pyaudio"
    echo "  Then re-run: ./run.sh web"
    return 1
}

ensure_venv() {
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        resolve_python
        if ! deps_core_ok "$PYTHON"; then
            echo "Installing dependencies in active virtualenv..."
            "$PYTHON" -m pip install -q "rns>=1.3.0" "aiohttp>=3.9.0"
        fi
        install_voice_deps "$PYTHON" || true
        return 0
    fi

    if [ -x "$VENV_PY" ] && deps_core_ok "$VENV_PY"; then
        PYTHON="$VENV_PY"
        install_voice_deps "$PYTHON" || true
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
    # macOS ships bash 3.2: empty "${array[@]}" with set -u triggers "unbound variable"
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
    install_voice_deps "$PYTHON" || true
    touch "$READY_MARK"
}

install_deps() {
    ensure_venv
}

case "${1:-}" in
    install)
        install_deps
        "$PYTHON" -m pip install -e .
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