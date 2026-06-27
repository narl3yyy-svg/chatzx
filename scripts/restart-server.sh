#!/usr/bin/env bash
# Detached chatxz restart — wait for the old server PID to exit, then re-launch.
set -euo pipefail

OLD_PID="${1:-}"
ROOT="${2:-}"
shift 2 || true
EXTRA=("$@")

if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
  echo "[restart] Invalid CHATXZ_ROOT" >&2
  exit 1
fi

if [ -n "$OLD_PID" ]; then
  for _ in $(seq 1 50); do
    kill -0 "$OLD_PID" 2>/dev/null || break
    sleep 0.1
  done
fi

STOP_SCRIPT="$ROOT/scripts/stop-chatxz.sh"
if [ -x "$STOP_SCRIPT" ]; then
  bash "$STOP_SCRIPT" || true
fi

sleep 0.3

export CHATXZ_ROOT="$ROOT"
export PYTHONPATH="$ROOT"
export PYTHON="${PYTHON:-python3}"

LAUNCH="$ROOT/scripts/launch-server.sh"
if [ -x "$LAUNCH" ]; then
  exec bash "$LAUNCH" "${EXTRA[@]:---share}"
fi

exec bash "$ROOT/run.sh" web "${EXTRA[@]:---share}"