#!/usr/bin/env bash
# Stop chatxz server and release ports 8742 (Rust HTTP), 8744 (RNS IPC), 4242 (RNS UDP).
set -euo pipefail

stop_pids() {
  local sig="$1"
  shift
  local pid
  for pid in "$@"; do
    [ -n "$pid" ] || continue
    kill "-$sig" "$pid" 2>/dev/null || true
  done
}

collect_pids() {
  local port="$1"
  local udp="${2:-0}"
  local pids=""

  if command -v lsof >/dev/null 2>&1; then
    if [ "$udp" = "1" ]; then
      pids="$(lsof -n -P -iUDP:"$port" -t 2>/dev/null || true)"
    else
      pids="$(lsof -n -P -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    fi
  elif command -v ss >/dev/null 2>&1; then
    local flag="-t"
    [ "$udp" = "1" ] && flag="-u"
    while IFS= read -r line; do
      for token in $line; do
        case "$token" in
          pid=*)
            pids="$pids ${token#pid=}"
            ;;
        esac
      done
    done < <(ss -H -n "$flag" -lp 2>/dev/null | grep ":$port " || true)
  fi
  echo "$pids"
}

for pattern in "chatxz-server" "target/release/chatxz" "chatxz.rnsd" "chatxz.web.server" "chatxz.app" "chatxz-web"; do
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    pkill -f "$pattern" 2>/dev/null || true
  fi
done

sleep 0.4

for port in 8742 8744; do
  stop_pids TERM $(collect_pids "$port" 0)
done
for port in 4242 8743; do
  stop_pids TERM $(collect_pids "$port" 1)
done

sleep 0.4

for port in 8742 8744; do
  stop_pids KILL $(collect_pids "$port" 0)
done
for port in 4242 8743; do
  stop_pids KILL $(collect_pids "$port" 1)
done

exit 0