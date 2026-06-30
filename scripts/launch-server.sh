#!/usr/bin/env bash
# Launch chatxz — Rust application (spawns RNS transport daemon automatically).
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
export CHATXZ_ROOT="$DIR"
export PYTHONPATH="$DIR"
CHATXZ_BIN="$DIR/target/release/chatxz"

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
    if [ -x "$CHATXZ_BIN" ]; then
        return 0
    fi
    echo "[rust] Building chatxz application..."
    (cd "$DIR" && cargo build --release -p chatxz-server)
}

launch_with_group() {
    local grp="$1"
    shift
    local cmd="cd $(printf '%q' "$DIR") && CHATXZ_ROOT=$(printf '%q' "$DIR") $(printf '%q' "$CHATXZ_BIN")"
    local arg
    for arg in "$@"; do
        cmd="$cmd $(printf '%q' "$arg")"
    done
    echo "[serial] Starting with active $grp group (sg $grp)"
    exec sg "$grp" -c "$cmd"
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

    build_rust

    for grp in dialout uucp; do
        if getent group "$grp" >/dev/null 2>&1 \
            && user_has_group "$grp" \
            && ! session_has_group "$grp" \
            && command -v sg >/dev/null 2>&1; then
            launch_with_group "$grp" "$@"
        fi
    done

    echo "[chatxz] Rust application on port 8742 (RNS daemon auto-started)"
    exec env CHATXZ_ROOT="$DIR" "$CHATXZ_BIN" "$@"
}

main "$@"