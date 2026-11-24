#!/usr/bin/env bash
# Build, package and release the Camouwolf browser.
#
# Usage:
#   bash scripts/release-camouwolf.sh [--target linux|macos|windows]
#                                 [--arch x86_64|arm64]
#                                 [--skip-build] [--no-release]
#
# Env:
#   CAMOUWOLF_BUILD_DIR   checkout location (default .camouwolf-build)
#   CAMOUWOLF_OUT         the out/ directory name (default Camouwolf)
#   CAMOUWOLF_RELEASE_TAG pin the tag (default v<version>-<YYYYMMDD>)
#   GITHUB_TOKEN      needed unless --no-release
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/browser/upstream.sh"

TARGET=linux
ARCH=x86_64
SKIP_BUILD=0
NO_RELEASE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)     TARGET="$2"; shift 2 ;;
    --arch)       ARCH="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --no-release) NO_RELEASE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

BUILD_DIR="${CAMOUWOLF_BUILD_DIR:-$ROOT/.camouwolf-build}"
SRC="$BUILD_DIR/chromium/src"
OUT="$SRC/out/${CAMOUWOLF_OUT:-Camouwolf}"
DIST="$ROOT/browser/dist"
PREFIX="camouwolf-${version}-${release}"

# One tag for every platform in a release. Deriving it per-invocation from the
# local date splits a multi-platform release across two tags when the builds
# land on either side of midnight — pin it once, here.
TAG="${CAMOUWOLF_RELEASE_TAG:-v${version}-$(date +%Y%m%d)}"

# BIN is what the build writes; the archive names follow the launchers'
# OS_ARCH_MATRIX (ci/targets.py archive_name).
case "$TARGET" in
  linux)   OS_SHORT=lin; BIN="$OUT/chrome" ;;
  macos)   OS_SHORT=mac; BIN="$OUT/Camouwolf.app/Contents/MacOS/Camouwolf" ;;
  windows) OS_SHORT=win; BIN="$OUT/chrome.exe" ;;
  *) echo "unknown target: $TARGET" >&2; exit 2 ;;
esac
case "$ARCH" in
  x86_64|arm64) ;;
  *) echo "unknown arch: $ARCH" >&2; exit 2 ;;
esac
ARCHIVE="${PREFIX}-${OS_SHORT}.${ARCH}.zip"

echo "==> Camouwolf $release on Chromium $version | target=$TARGET $ARCH | tag=$TAG"

# ------------------------------------------------------------------ 1. build
if [ "$SKIP_BUILD" = 0 ]; then
  CAMOUWOLF_TARGET="$TARGET" CAMOUWOLF_ARCH="$ARCH" bash "$ROOT/browser/scripts/build.sh" "$BUILD_DIR"
else
  echo "--> --skip-build: reusing $OUT"
fi
[ -f "$BIN" ] || { echo "!! no binary at $BIN" >&2; exit 1; }
[ ! -e "$OUT/.camouwolf-unfinished" ] || { echo "!! $OUT is an unfinished build" >&2; exit 1; }

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

# The runtime payload. The executable is renamed `camouwolf` so a `ps` listing
# on a scraping box does not advertise a bare `chrome` alongside a pile of
# automation; the launchers look for that name (pkgman.py LAUNCH_FILE).
# RES is where the launchers look for the files the package adds.
case "$TARGET" in
  linux|windows)
    RES="$PKG"
    EXE=camouwolf; [ "$TARGET" = windows ] && EXE=camouwolf.exe
    cp "$BIN" "$PKG/$EXE"
    # Windows: chrome.exe loads chrome.dll and chrome_elf.dll from its own
    # directory, and its manifest names the <version>.manifest beside it.
    for f in chrome_crashpad_handler chrome_100_percent.pak chrome_200_percent.pak \
             resources.pak icudtl.dat v8_context_snapshot.bin snapshot_blob.bin \
             libEGL.so libGLESv2.so libvulkan.so.1 libvk_swiftshader.so \
             vk_swiftshader_icd.json "$version.manifest" \
             chrome.dll chrome_elf.dll chrome_wer.dll libEGL.dll libGLESv2.dll \
             vk_swiftshader.dll vulkan-1.dll d3dcompiler_47.dll dxcompiler.dll dxil.dll; do
      [ -e "$OUT/$f" ] && cp -a "$OUT/$f" "$PKG/" || true
    done
    [ -d "$OUT/locales" ] && cp -a "$OUT/locales" "$PKG/"
    ;;
  macos)
    # The bundle as built, with its executable renamed in Info.plist too: the
    # framework, helpers and resources are found relative to the bundle.
    cp -a "$OUT/Camouwolf.app" "$PKG/Camouwolf.app"
    mv "$PKG/Camouwolf.app/Contents/MacOS/Camouwolf" "$PKG/Camouwolf.app/Contents/MacOS/camouwolf"
    python3 - "$PKG/Camouwolf.app/Contents/Info.plist" <<'PY'
import plistlib, sys
path = sys.argv[1]
with open(path, "rb") as fh:
    info = plistlib.load(fh)
info["CFBundleExecutable"] = "camouwolf"
with open(path, "wb") as fh:
    plistlib.dump(info, fh)
PY
    RES="$PKG/Camouwolf.app/Contents/Resources"
    ;;
esac

# The launcher reads version.json to decide which milestone the identity should
# claim and to enforce the CONSTRAINTS range. A bundle without it launches with
# the launcher's pinned fallback, which is right only by luck. `product` is
# "chromium" unless an overlay that emulates Chrome set it in upstream.sh; the
# launcher claims Chrome's network voices only for "chrome".
cat > "$RES/version.json" <<JSON
{
  "version": "${version}",
  "release": "${release}",
  "milestone": ${milestone},
  "target": "${TARGET}",
  "arch": "${ARCH}",
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
cp -a "$ROOT/browser/bundle/fonts" "$RES/fonts"
cp -a "$ROOT/browser/bundle/fontconfig" "$RES/fontconfig"

# -y keeps symlinks as symlinks: a macOS framework is built from them, and
# following them would store the framework twice and break its layout.
( cd "$STAGE" && zip -qry "$DIST/$ARCHIVE" "$PREFIX" )
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
