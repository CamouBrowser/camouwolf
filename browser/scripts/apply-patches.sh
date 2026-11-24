#!/usr/bin/env bash
# Copy Camouwolf's additions into a chromium/src checkout and apply the patch series.
#
# Usage:  bash browser/scripts/apply-patches.sh /path/to/chromium/src
#
# Idempotent-ish: it refuses to run twice on the same tree unless you pass
# --force, because GNU patch will happily apply a series on top of itself and
# leave a tree that compiles but is silently double-patched. (Camoufox lost a build
# to exactly that; the guard is cheap.)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${1:?usage: apply-patches.sh /path/to/chromium/src [--force]}"
FORCE="${2:-}"
# shellcheck source=/dev/null
source "$REPO/browser/upstream.sh"

STAMP="$SRC/.camouwolf-patched"
if [ -f "$STAMP" ] && [ "$FORCE" != "--force" ]; then
  echo "==> tree already patched at $(cat "$STAMP")" >&2
  echo "    re-run with --force after 'git checkout -f tags/$version' to reapply." >&2
  exit 1
fi

echo "==> Camouwolf $release on Chromium $version -> $SRC"

# 1. additions (new files, not diffs). base/camouwolf_config.* and base/camouwolf_farble.h
#    are shipped whole rather than as `new file` hunks: a 200-line file expressed
#    as a diff is unreadable in review and conflicts pointlessly on every bump.
#
#    Installed only when the bytes actually differ. ninja keys on mtime, so an
#    unconditional rewrite marks these dirty on every run -- and camouwolf_config.h
#    is included right across the series, so that one touch invalidates most of
#    the objdir and turns a warm rebuild into a near-full one. Costs a cmp;
#    saves hours on the incremental path, which is where this runs most.
echo "--> additions"
install_if_changed() {
  if [ -f "$2" ] && cmp -s "$1" "$2"; then
    return 0
  fi
  install -Dm644 "$1" "$2"
}
install_if_changed "$REPO/browser/additions/camouwolfcfg/camouwolf_config.h" "$SRC/base/camouwolf_config.h"
install_if_changed "$REPO/browser/additions/camouwolfcfg/camouwolf_config.cc" "$SRC/base/camouwolf_config.cc"
install_if_changed "$REPO/browser/additions/camouwolfcfg/camouwolf_farble.h" "$SRC/base/camouwolf_farble.h"

# The ucrtbase replica. Shipped whole for the same reason as camouwolf_config: it is
# ~1900 lines of transcribed lookup tables and polynomial kernels that no one
# will ever review as a diff, and it has no upstream counterpart to conflict
# with on a bump. Header-only, so there is no BUILD.gn edit to go with it.
install_if_changed "$REPO/browser/additions/camouwolfucrt/camouwolf_ucrt_math.h" "$SRC/base/camouwolf/camouwolf_ucrt_math.h"
install_if_changed "$REPO/browser/additions/camouwolfucrt/camouwolf_ucrt_atan2_tables.h" "$SRC/base/camouwolf/camouwolf_ucrt_atan2_tables.h"
install_if_changed "$REPO/browser/additions/camouwolfucrt/camouwolf_ucrt_pow_tables.h" "$SRC/base/camouwolf/camouwolf_ucrt_pow_tables.h"

# A proxied context's WebRTC (patch 0031): a SOCKS5 UDP relay socket and the
# identity's network, whole files with no upstream counterpart.
install_if_changed "$REPO/browser/additions/camouwolfp2p/camouwolf_proxied_p2p.h" "$SRC/services/network/p2p/camouwolf_proxied_p2p.h"
install_if_changed "$REPO/browser/additions/camouwolfp2p/camouwolf_proxied_p2p.cc" "$SRC/services/network/p2p/camouwolf_proxied_p2p.cc"

# 2. branding art
#
# browser/branding/ mirrors chrome/app/theme/ one-for-one, so this is a plain
# overlay: every file lands on the upstream asset it replaces. Regenerate it
# with `python3 browser/scripts/generate-branding.py` after the logo changes.
#
# Unlike the additions above, these OVERWRITE upstream files rather than adding
# new ones — which means a bump that renames or moves an asset would otherwise
# leave us writing a Camouwolf logo to a path nothing reads, and shipping a browser
# still wearing the Chromium ball. Nothing would fail; the icon would just
# quietly not change. So an unexpectedly-absent destination is a hard error.
#
# The exception list is for paths we add on purpose. default_200_percent/
# chromium/linux/ does not exist upstream: grit's fallback_to_low_resolution
# upscales the 1x art there instead, and supplying real 2x art is a strict
# improvement rather than a replacement.
#
# README.md is excluded from the walk because it is documentation for the
# branding tree, and chrome/app/theme/README.md is a real upstream file it would
# otherwise clobber.
echo "--> branding"
BRANDING_NEW_PATHS="
default_200_percent/chromium/linux/product_logo_16.png
default_200_percent/chromium/linux/product_logo_32.png
"
branded=0
while IFS= read -r -d '' src; do
  rel="${src#"$REPO/browser/branding/"}"
  dest="$SRC/chrome/app/theme/$rel"
  if [ ! -f "$dest" ] && ! grep -qxF "$rel" <<<"$BRANDING_NEW_PATHS"; then
    echo "!! branding target vanished upstream: chrome/app/theme/$rel" >&2
    echo "   Chromium moved or renamed this asset. Reconcile browser/branding/" >&2
    echo "   and BRANDING_PNGS in browser/scripts/generate-branding.py before" >&2
    echo "   building, or the build ships Chromium's logo." >&2
    exit 1
  fi
  install_if_changed "$src" "$dest"
  branded=$((branded + 1))
done < <(find "$REPO/browser/branding" -type f ! -name README.md -print0)
echo "    $branded branding files installed"

# 3. patch series
echo "--> patches"
applied=0
while read -r line; do
  case "$line" in ''|\#*) continue ;; esac
  patch_file="$REPO/browser/patches/$line"
  [ -f "$patch_file" ] || { echo "!! missing $line" >&2; exit 1; }
  if ! git -C "$SRC" apply -p1 --whitespace=nowarn "$patch_file"; then
    echo "!! FAILED: $line" >&2
    echo "   Run 'python3 scripts/check-dead-patches.py' to see which hunks moved." >&2
    exit 1
  fi
  printf '    %-44s ok\n' "$line"
  applied=$((applied + 1))
done < "$REPO/browser/patches/series"

date -u +"%Y-%m-%dT%H:%M:%SZ ${version} ${release}" > "$STAMP"
echo "==> $applied patches applied"
