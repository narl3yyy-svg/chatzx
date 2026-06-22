#!/usr/bin/env bash
# Grant the current user access to USB serial ports (ttyUSB*, ttyACM*, etc.)
set -euo pipefail

if [ "$EUID" -eq 0 ]; then
    echo "Run as your normal user, not root." >&2
    exit 1
fi

added=0
for grp in dialout uucp; do
    if getent group "$grp" >/dev/null 2>&1; then
        if id -nG "$USER" | tr ' ' '\n' | grep -qx "$grp"; then
            echo "Already in group: $grp"
        else
            sudo usermod -aG "$grp" "$USER"
            echo "Added $USER to group: $grp"
            added=1
        fi
    fi
done

if [ "$added" -eq 1 ]; then
    echo ""
    echo "Log out and back in (or reboot) so serial port permissions take effect."
else
    echo ""
    echo "Serial groups already configured for $USER."
fi