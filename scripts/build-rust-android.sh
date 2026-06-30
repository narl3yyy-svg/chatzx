#!/usr/bin/env bash
# Cross-compile chatxz-server for Android arm64 (Termux/adb sideload or APK assets).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-aarch64-linux-android}"
OUT_DIR="$ROOT/android/app/src/main/assets/bin"
mkdir -p "$OUT_DIR"

if ! rustup target list --installed | grep -qx "$TARGET"; then
  echo "Adding Rust target $TARGET..."
  rustup target add "$TARGET"
fi

# NDK path — set ANDROID_NDK_HOME or install via Android Studio.
if [ -z "${ANDROID_NDK_HOME:-}" ]; then
  echo "Set ANDROID_NDK_HOME to your Android NDK (e.g. \$HOME/Android/Sdk/ndk/<version>)"
  exit 1
fi

export CC="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/${TARGET}30-clang"
export CARGO_TARGET_${TARGET^^}_LINKER="$CC"

echo "Building chatxz-server for $TARGET..."
(cd "$ROOT" && cargo build --release -p chatxz-server --target "$TARGET")

BIN="$ROOT/target/$TARGET/release/chatxz-server"
cp "$BIN" "$OUT_DIR/chatxz-server"
chmod +x "$OUT_DIR/chatxz-server"
echo "Bundled: $OUT_DIR/chatxz-server"