#!/usr/bin/env bash
# Build, package and release the Camouwolf browser.
#
# Usage:
#   bash scripts/release-camouwolf.sh [--target linux|macos|windows]
#                                 [--skip-build] [--no-release]
#
# Env:
#   CAMOUWOLF_BUILD_DIR   checkout location (default .camouwolf-build)
#   CAMOUWOLF_RELEASE_TAG pin the tag (default v<version>-<YYYYMMDD>)
#   GITHUB_TOKEN      needed unless --no-release
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/browser/upstream.sh"

TARGET=linux
SKIP_BUILD=0
NO_RELEASE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)     TARGET="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --no-release) NO_RELEASE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

BUILD_DIR="${CAMOUWOLF_BUILD_DIR:-$ROOT/.camouwolf-build}"
SRC="$BUILD_DIR/chromium/src"
OUT="$SRC/out/Camouwolf"
DIST="$ROOT/browser/dist"
PREFIX="camouwolf-${version}-${release}"

# One tag for every platform in a release. Deriving it per-invocation from the
# local date splits a multi-platform release across two tags when the builds
# land on either side of midnight — pin it once, here.
TAG="${CAMOUWOLF_RELEASE_TAG:-v${version}-$(date +%Y%m%d)}"

case "$TARGET" in
  linux)   ARCHIVE="${PREFIX}-lin.x86_64.zip" ;;
  macos)   ARCHIVE="${PREFIX}-mac.arm64.zip" ;;
  windows) ARCHIVE="${PREFIX}-win.x86_64.zip" ;;
  *) echo "unknown target: $TARGET" >&2; exit 2 ;;
esac

echo "==> Camouwolf $release on Chromium $version | target=$TARGET | tag=$TAG"

# ------------------------------------------------------------------ 1. build
if [ "$SKIP_BUILD" = 0 ]; then
  bash "$ROOT/browser/scripts/build.sh" "$BUILD_DIR"
else
  echo "--> --skip-build: reusing $OUT"
  [ -x "$OUT/chrome" ] || { echo "!! no binary at $OUT/chrome" >&2; exit 1; }
fi

# ---------------------------------------------------------------- 2. package
echo "--> packaging"
# Stage on REAL DISK, next to the output, not in $TMPDIR.
#
# A bare `mktemp -d` lands in /tmp, and /tmp is a tmpfs on plenty of desktops —
# 31 GB here, and usually most of the way full. The payload is a ~530 MB binary
# plus the font bundle (~2 GB unpacked), which overruns it and
# fails mid-copy with "Disk quota exceeded" on a random font file. That reads as
# a corrupt font cache rather than as a full ramdisk. browser/scripts/build.sh
# already warns about exactly this trap for the link step; packaging has it too.
mkdir -p "$DIST"
STAGE="$(mktemp -d -p "$DIST" .stage-XXXXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/$PREFIX"
mkdir -p "$PKG"

# The runtime payload. `chrome` is renamed to `camouwolf` so a `ps` listing on a
# scraping box does not advertise a bare `chrome` alongside a pile of
# automation — cheap, and it costs nothing.
cp "$OUT/chrome" "$PKG/camouwolf"
for f in chrome_crashpad_handler chrome_100_percent.pak chrome_200_percent.pak \
         resources.pak icudtl.dat v8_context_snapshot.bin snapshot_blob.bin \
         libEGL.so libGLESv2.so libvulkan.so.1 libvk_swiftshader.so \
         vk_swiftshader_icd.json; do
  [ -e "$OUT/$f" ] && cp -a "$OUT/$f" "$PKG/" || true
done
[ -d "$OUT/locales" ] && cp -a "$OUT/locales" "$PKG/"

# The launcher reads version.json to decide which milestone the identity should
# claim and to enforce the CONSTRAINTS range. A bundle without it launches with
# the launcher's pinned fallback, which is right only by luck. `product` is
# "chromium" unless an overlay that emulates Chrome set it in upstream.sh; the
# launcher claims Chrome's network voices only for "chrome".
cat > "$PKG/version.json" <<JSON
{
  "version": "${version}",
  "release": "${release}",
  "milestone": ${milestone},
  "target": "${TARGET}",
  "product": "${product:-chromium}"
}
JSON

# Desktop launcher, Linux only. The zip cannot ship a system .desktop entry —
# Exec/Icon can't be known until someone unzips it — so ship the template plus
# the hicolor icons and let install-desktop.sh resolve the paths in place.
# Without this the bundle still has the right window icon (that is compiled into
# the .pak) but nothing in the dock or the app grid.
if [ "$TARGET" = linux ]; then
  cp -a "$ROOT/browser/settings/linux-desktop" "$PKG/linux-desktop"
  chmod +x "$PKG/linux-desktop/install-desktop.sh"
fi

# Font bundle + fontconfig. Required: the launcher claims fonts on the
# understanding that this exact bundle ships beside the binary, so a package
# without it would claim fonts it cannot render.
if ! python3 "$ROOT/browser/scripts/fetch-fonts.py" --check; then
  echo "!! browser/bundle/fonts/ is not the pinned font bundle."
  echo "   Fetch it with: python3 browser/scripts/fetch-fonts.py --extract"
  exit 1
fi
cp -a "$ROOT/browser/bundle/fonts" "$PKG/fonts"
cp -a "$ROOT/browser/bundle/fontconfig" "$PKG/fontconfig"

( cd "$STAGE" && zip -qr "$DIST/$ARCHIVE" "$PREFIX" )
( cd "$DIST" && sha256sum "$ARCHIVE" > "$ARCHIVE.sha256" )
echo "--> $DIST/$ARCHIVE ($(du -h "$DIST/$ARCHIVE" | cut -f1))"

# ----------------------------------------------------------------- 3. release
if [ "$NO_RELEASE" = 1 ]; then
  echo "==> --no-release: stopping before publish"
  exit 0
fi

command -v gh >/dev/null || { echo "!! gh CLI not found" >&2; exit 1; }
if ! gh release view "$TAG" >/dev/null 2>&1; then
  gh release create "$TAG" \
    --title "Camouwolf $version-$release" \
    --notes "Camouwolf $release on Chromium $version." \
    --draft=false
fi
gh release upload "$TAG" "$DIST/$ARCHIVE" "$DIST/$ARCHIVE.sha256" --clobber
echo "==> released $TAG"
