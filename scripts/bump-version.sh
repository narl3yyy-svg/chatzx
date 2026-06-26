#!/usr/bin/env bash
# Bump chatxz version in version.properties, chatxz/_version.py, and Android Gradle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROPS="$ROOT/version.properties"
GRADLE="$ROOT/android/app/build.gradle.kts"

usage() {
  echo "Usage: $0 <version> [version_code]"
  echo "  e.g. $0 0.3.29       # auto-increment versionCode"
  echo "       $0 0.3.29 29    # explicit versionCode"
  exit 1
}

[[ $# -ge 1 ]] || usage

NEW_NAME="$1"
if [[ ! "$NEW_NAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must look like MAJOR.MINOR.PATCH (got: $NEW_NAME)" >&2
  exit 1
fi

CURRENT_CODE="$(grep -E '^VERSION_CODE=' "$PROPS" | cut -d= -f2)"
if [[ $# -ge 2 ]]; then
  NEW_CODE="$2"
else
  NEW_CODE=$((CURRENT_CODE + 1))
fi

cat > "$PROPS" <<EOF
VERSION_NAME=$NEW_NAME
VERSION_CODE=$NEW_CODE

EOF

cat > "$ROOT/chatxz/_version.py" <<EOF
"""App version — keep in sync with version.properties via scripts/bump-version.sh."""

__version__ = "$NEW_NAME"
__version_code__ = $NEW_CODE

EOF

sed -i "s/val releaseVersionNameForCi = \"[^\"]*\"/val releaseVersionNameForCi = \"$NEW_NAME\"/" "$GRADLE"
sed -i "s/val releaseVersionCodeForCi = [0-9]\\+/val releaseVersionCodeForCi = $NEW_CODE/" "$GRADLE"

sed -i "s/^version = \"[^\"]*\"/version = \"$NEW_NAME\"/" "$ROOT/pyproject.toml"
sed -i "s/^    version=\"[^\"]*\"/    version=\"$NEW_NAME\"/" "$ROOT/setup.py"

bash "$ROOT/scripts/sync-android.sh" >/dev/null

echo "Bumped to $NEW_NAME (versionCode $NEW_CODE)"
echo "  version.properties  -> Gradle versionName/versionCode"
echo "  chatxz/_version.py -> server APP_VERSION"
echo "  android/app/build.gradle.kts -> CI release metadata"
echo "  android Python bundle synced via scripts/sync-android.sh"