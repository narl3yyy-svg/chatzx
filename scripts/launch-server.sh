#!/usr/bin/env bash
# Launch chatxz v2: Rust primary (8742) + Python RNS backend (8743).
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
export PYTHONPATH="$DIR"
export CHATXZ_ROOT="$DIR"

PUBLIC_PORT=8742
INTERNAL_PORT=8743
RUST_BIN="$DIR/target/release/chatxz-server"

user_has_group() {
    id -Gn "${USER:?}" 2>/dev/null | tr ' ' '\n' | grep -qx "$1"
}

session_has_group() {
    id -nG 2>/dev/null | tr ' ' '\n' | grep -qx "$1"
}

ensure_serial_groups() {
    local setup="$DIR/scripts/setup-serial-perms.sh"
    if user_has_group dialout || user_has_group uucp; then
        return 0
    fi
    if ! getent group dialout >/dev/null 2>&1 && ! getent group uucp >/dev/null 2>&1; then
        return 0
    fi
    echo "[serial] USB serial needs dialout group membership."
    if [ -x "$setup" ]; then
        bash "$setup" || true
    else
        echo "[serial] Run: sudo usermod -aG dialout $USER"
    fi
}

build_rust() {
    if [ -x "$RUST_BIN" ]; then
        return 0
    fi
    echo "[rust] Building chatxz-server (release)..."
    (cd "$DIR" && cargo build --release -p chatxz-server)
}

PY_PID=""

cleanup() {
    if [ -n "$PY_PID" ] && kill -0 "$PY_PID" 2>/dev/null; then
        kill "$PY_PID" 2>/dev/null || true
        wait "$PY_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

launch_python_backend() {
    local cmd="cd $(printf '%q' "$DIR") && PYTHONPATH=$(printf '%q' "$DIR") CHATXZ_ROOT=$(printf '%q' "$DIR") $(printf '%q' "$PYTHON") -m chatxz.web.server --internal --port $INTERNAL_PORT --public-port $PUBLIC_PORT"
    local arg
    for arg in "$@"; do
        cmd="$cmd $(printf '%q' "$arg")"
    done
    bash -c "$cmd" &
    PY_PID=$!
    echo "[python] RNS backend pid=$PY_PID (port $INTERNAL_PORT)"
}

launch_with_group() {
    local grp="$1"
    shift
    ensure_serial_groups
    if command -v stty >/dev/null 2>&1 && [ -t 0 ]; then
        stty susp undef 2>/dev/null || true
    fi
    STOP_SCRIPT="$DIR/scripts/stop-chatxz.sh"
    if [ -x "$STOP_SCRIPT" ]; then
        bash "$STOP_SCRIPT" || true
    fi
    build_rust
    launch_python_backend "$@"
    echo "[rust] Primary server on port $PUBLIC_PORT"
    exec sg "$grp" -c "cd $(printf '%q' "$DIR") && CHATXZ_ROOT=$(printf '%q' "$DIR") $(printf '%q' "$RUST_BIN") --port $PUBLIC_PORT --backend http://127.0.0.1:$INTERNAL_PORT"
}

main() {
    ensure_serial_groups

    if command -v stty >/dev/null 2>&1 && [ -t 0 ]; then
        stty susp undef 2>/dev/null || true
    fi

    STOP_SCRIPT="$DIR/scripts/stop-chatxz.sh"
    if [ -x "$STOP_SCRIPT" ]; then
        bash "$STOP_SCRIPT" || true
    fi

    for grp in dialout uucp; do
        if getent group "$grp" >/dev/null 2>&1 \
            && user_has_group "$grp" \
            && ! session_has_group "$grp" \
            && command -v sg >/dev/null 2>&1; then
            launch_with_group "$grp" "$@"
        fi
    done

    build_rust
    launch_python_backend "$@"
    echo "[rust] Primary server on port $PUBLIC_PORT"
    exec env CHATXZ_ROOT="$DIR" "$RUST_BIN" --port "$PUBLIC_PORT" --backend "http://127.0.0.1:$INTERNAL_PORT"
}

main "$@"