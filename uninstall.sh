#!/usr/bin/env bash
# chatxz uninstaller — removes app data chatxz creates (with prompts).
set -euo pipefail

echo "=== chatxz Uninstaller ==="
echo

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/chatxz"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/chatxz"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/chatxz"
PORTABLE_DATA=""

if [ -n "${CHATXZ_PORTABLE:-}" ]; then
    PORTABLE_DATA="$CHATXZ_PORTABLE/chatxz-data"
elif [ -d "./chatxz-data" ]; then
    PORTABLE_DATA="$(cd "./chatxz-data" && pwd)"
fi

stop_chatxz_processes() {
    echo "[1/5] Stopping running chatxz / RNS processes..."
    local stopped=0
    for pattern in "chatxz.web.server" "chatxz.app" "run.sh web"; do
        if pgrep -f "$pattern" >/dev/null 2>&1; then
            pkill -f "$pattern" 2>/dev/null || true
            stopped=1
        fi
    done
    if [ "$stopped" -eq 1 ]; then
        sleep 1
        echo "  Stopped chatxz server process(es)"
    else
        echo "  No chatxz server process found"
    fi
}

remove_dir_prompt() {
    local label="$1"
    local path="$2"
    if [ ! -e "$path" ]; then
        return 0
    fi
    echo "  $label: $path"
    read -r -p "    Remove? (y/N) " reply
    if [[ $reply =~ ^[Yy]$ ]]; then
        if rm -rf "$path"; then
            echo "    Removed."
        else
            echo "    FAILED to remove (check permissions)."
            return 1
        fi
    else
        echo "    Kept."
    fi
}

cleanup_rns_sockets() {
    echo "[4/5] Cleaning stale RNS sockets in /tmp/rns ..."
    local count=0
    if [ -d /tmp/rns ]; then
        while IFS= read -r -d '' sock; do
            rm -f "$sock" 2>/dev/null && count=$((count + 1)) || true
        done < <(find /tmp/rns -name socket -print0 2>/dev/null)
        find /tmp/rns -type d -empty -delete 2>/dev/null || true
    fi
    echo "  Removed $count stale socket(s)"
}

stop_chatxz_processes

if command -v pipx &>/dev/null; then
    echo "[2/5] Removing pipx package..."
    if pipx uninstall chatxz 2>/dev/null; then
        echo "  Removed chatxz from pipx"
    else
        echo "  chatxz not found in pipx (already removed)"
    fi
else
    echo "[2/5] pipx not found, skipping package removal"
fi

echo "[3/5] Application data (identity, settings, chat history, RNS config):"
remove_dir_prompt "Config" "$CONFIG_DIR"
remove_dir_prompt "Data" "$DATA_DIR"
remove_dir_prompt "Cache" "$CACHE_DIR"
if [ -n "$PORTABLE_DATA" ]; then
    remove_dir_prompt "Portable data" "$PORTABLE_DATA"
fi

cleanup_rns_sockets

echo "[5/5] Checking for leftover binaries..."
LEFTOVER=0
for bin in chatxz chatxz-web; do
    if command -v "$bin" &>/dev/null; then
        echo "  WARNING: $bin still found at $(command -v "$bin")"
        LEFTOVER=1
    fi
done
if [ "$LEFTOVER" -eq 0 ]; then
    echo "  No leftover binaries found."
fi

echo
echo "=== Uninstall complete ==="
echo "To reinstall: ./install.sh  or  ./run.sh web --share"