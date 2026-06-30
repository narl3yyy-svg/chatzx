#!/usr/bin/env bash
# Build the Rust chatxz-media Python extension (optional but recommended for call quality).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/chatxz-media"
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
if command -v maturin >/dev/null 2>&1; then
  maturin develop --release
elif command -v pipx >/dev/null 2>&1; then
  pipx run maturin develop --release
else
  echo "Install maturin: pip install maturin"
  exit 1
fi
echo "chatxz-media installed. Restart chatxz to use the Rust media engine."