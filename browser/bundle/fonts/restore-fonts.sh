#!/usr/bin/env bash
# Restore the bundled cross-OS fonts from the local cache.
#
# Usage:  bash browser/bundle/fonts/restore-fonts.sh [linux|macos|windows ...]
# Env:    CAMOUWOLF_FONT_CACHE  (default: the shared Holo font cache)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Shared with Holo on purpose: the font set is a property of the OS, not of the
# browser, so both projects want identical bytes. Diverging would make a Camouwolf
# Windows persona and a Holo Windows persona report different font lists from
# the same claimed machine.
CACHE="${CAMOUWOLF_FONT_CACHE:-${HOLO_FONT_CACHE:-$HOME/jobharvest/holo-font-cache}}"

# The cache holds one tar per OS (fonts-<os>.tar), the layout seed-font-cache.sh
# writes. This used to `rsync` the cache in wholesale, which copied the TARBALLS
# into the bundle and left zero usable fonts -- and then reported "0 font files"
# and exited 0, so a packaging build silently produced a Windows persona with no
# Windows fonts. Extract, and fail loudly if a tar is missing.
OSES=("$@")
[ "${#OSES[@]}" -eq 0 ] && OSES=(linux macos windows)

if [ ! -d "$CACHE" ]; then
  echo "!! font cache not found: $CACHE" >&2
  echo "   Set CAMOUWOLF_FONT_CACHE to the directory holding fonts-<os>.tar." >&2
  exit 1
fi

echo "==> restoring fonts from $CACHE"
for os in "${OSES[@]}"; do
  tarball="$CACHE/fonts-$os.tar"
  if [ ! -f "$tarball" ]; then
    echo "!! missing $tarball" >&2
    exit 1
  fi
  echo "    $os"
  rm -rf "${HERE:?}/$os"
  tar -C "$HERE" -xf "$tarball"
done

total=0
for os in "${OSES[@]}"; do
  n=$(find "$HERE/$os" -type f \( -name '*.ttf' -o -name '*.ttc' -o -name '*.otf' \) 2>/dev/null | wc -l)
  printf '    %-8s %s font file(s)\n' "$os" "$n"
  total=$((total + n))
done
if [ "$total" -eq 0 ]; then
  echo "!! extracted 0 font files — the cache tars are empty or the wrong shape" >&2
  exit 1
fi
echo "==> $total font file(s) restored"
