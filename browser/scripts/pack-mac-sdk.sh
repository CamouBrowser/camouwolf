#!/usr/bin/env bash
# Packs the part of Xcode a Linux host needs to cross-compile Chromium for
# macOS: Contents/version.plist and the MacOSX SDK, in Xcode.app's layout
# (build.sh's CAMOUWOLF_MAC_SDK_DIR). Runs on a Mac. It picks the installed
# Xcode whose SDK is one the pinned Chromium asks for (build/config/mac/mac_sdk.gni).
# The SDK is Apple's licensed software: never publish it.
#
# Usage:  bash browser/scripts/pack-mac-sdk.sh <out.tar.gz>
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO/browser/upstream.sh"
OUT="${1:?out.tar.gz}"

WANT="$(curl -fsSL "https://chromium.googlesource.com/chromium/src/+/refs/tags/$version/build/config/mac/mac_sdk.gni?format=TEXT" \
  | base64 -d | sed -nE 's/^ *mac_sdk_official(_universal)?_version = "(.*)"/\2/p')"
SDKS=Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs
for app in /Applications/Xcode*.app; do
  v="$(DEVELOPER_DIR="$app/Contents/Developer" xcrun --sdk macosx --show-sdk-version 2>/dev/null)" || continue
  if grep -qxF "$v" <<<"$WANT"; then
    echo "==> $app: MacOSX $v"
    tar -C "$app" -czf "$OUT" Contents/version.plist "$SDKS/MacOSX.sdk"
    exit 0
  fi
done
echo "!! no Xcode here has a MacOSX SDK in: $(echo $WANT)" >&2
exit 1
