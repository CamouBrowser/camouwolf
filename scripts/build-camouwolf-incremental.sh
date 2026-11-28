#!/usr/bin/env bash
# Fast iteration on a warm Chromium tree: reset the patched files, re-apply the
# series, and let ninja rebuild only what changed (~10-20 min instead of hours).
#
# The whole point is that ninja's dependency graph survives. So this does NOT
# `gclient sync` and does NOT `gn gen` unless args.gn actually changed — either
# would invalidate the objdir and turn a 15-minute rebuild into a cold one.
#
# Usage:  bash scripts/build-camouwolf-incremental.sh
# Env:    CAMOUWOLF_BUILD_DIR (default .camouwolf-build), and CAMOUWOLF_TARGET,
#         CAMOUWOLF_ARCH, CAMOUWOLF_OUT as browser/scripts/build.sh reads them
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/browser/upstream.sh"

BUILD_DIR="${CAMOUWOLF_BUILD_DIR:-$ROOT/.camouwolf-build}"
SRC="$BUILD_DIR/chromium/src"
OUT_NAME="${CAMOUWOLF_OUT:-Camouwolf}"
OUT="$SRC/out/$OUT_NAME"
TARGET="${CAMOUWOLF_TARGET:-linux}"
ARCH="${CAMOUWOLF_ARCH:-x86_64}"

[ -d "$SRC" ] || {
  echo "!! no warm tree at $SRC — run browser/scripts/build.sh first." >&2
  exit 1
}
export PATH="$BUILD_DIR/depot_tools:$PATH"
export DEPOT_TOOLS_UPDATE=0
cd "$SRC"

# Revert ONLY the files the series touches. `git checkout -f .` across the whole
# tree would also discard gclient's DEPS-managed files and force a resync.
echo "==> reverting patched files"
# -h is load-bearing: without it grep prefixes each match with "<file>:", so the
# `^` in the lookbehind no longer anchors to the diff line and the list comes
# back EMPTY. Nothing gets reverted, the series is then re-applied on top of an
# already-patched tree, and the first patch fails with "does not apply" — which
# reads like patch drift rather than a scripting bug.
# .camouwolf-patched-files is what the last apply patched; a patch removed since
# then is reverted through it.
mapfile -t FILES < <({ grep -hoP '(?<=^diff --git a/)\S+' "$ROOT"/browser/patches/*.patch; cat .camouwolf-patched-files 2>/dev/null; } | sort -u)
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "!! no patched files parsed out of browser/patches/ — refusing to continue" >&2
  exit 1
fi
echo "    reverting ${#FILES[@]} file(s)"

# ninja keys on mtime, NOT content. Reverting and re-applying rewrites all of
# these files even when the series did not change a byte of them, so every one
# looks dirty and the "warm" rebuild becomes a near-cold one. Observed on a
# 64-core build host: a single-patch edit queued 52044 targets and was still going six
# hours later.
#
# So stash the pre-revert state, and afterwards put the ORIGINAL mtime back on
# every file that round-tripped byte-identical. A file whose content genuinely
# changed keeps its fresh mtime and gets rebuilt — that is the point of the
# script, and this must never suppress it.
STASH="$(mktemp -d)"
trap 'rm -rf "$STASH"' EXIT
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  mkdir -p "$STASH/$(dirname "$f")"
  cp -p "$f" "$STASH/$f"
done

# A file a patch creates is deleted, so the patch can create it again; every
# other file is checked out of the repository that holds it, which for v8/ and
# third_party/skia/ is their own, not chromium/src.
mapfile -t CREATED < <({ awk '/^diff --git a\//{f=substr($3,3)} /^new file mode/{print f}' "$ROOT"/browser/patches/*.patch; cat .camouwolf-created-files 2>/dev/null; } | sort -u)
for f in "${FILES[@]}"; do
  if printf '%s\n' "${CREATED[@]}" | grep -qxF -- "$f"; then
    rm -f -- "$f"
  elif [ -e "$f" ]; then
    git -C "$(dirname "$f")" checkout -f -- "$(basename "$f")"
  fi
done
rm -f .camouwolf-patched

echo "==> re-applying the series"
bash "$ROOT/browser/scripts/apply-patches.sh" "$SRC" --force

# cmp against the stash, not against git: the question is "did this file change
# since ninja last saw it", and ninja last saw the PATCHED tree.
kept=0
for f in "${FILES[@]}"; do
  [ -f "$STASH/$f" ] && [ -f "$f" ] || continue
  if cmp -s "$STASH/$f" "$f"; then
    touch -r "$STASH/$f" "$f"
    kept=$((kept + 1))
  fi
done
echo "    ${kept}/${#FILES[@]} file(s) unchanged — mtime restored, ninja will skip them"

# gn gen only when the args moved. Re-running it needlessly rewrites build.ninja
# and can cascade into a much larger rebuild than the edit justified.
ARGS_STAMP="$OUT/.camouwolf-args-sha"
GN_ARGS="$(bash "$ROOT/browser/scripts/gn-args.sh" "$TARGET" "$ARCH")"
NEW_SHA="$(sha256sum <<<"$GN_ARGS" | cut -d' ' -f1)"
if [ ! -f "$ARGS_STAMP" ] || [ "$(cat "$ARGS_STAMP")" != "$NEW_SHA" ]; then
  echo "==> the GN args changed — regenerating"
  gn gen "out/$OUT_NAME" --args="$GN_ARGS"
  echo "$NEW_SHA" > "$ARGS_STAMP"
fi

# Same caps as the full build, and for the same reason — this script gets run on
# a shared box far more often than the full one does, so it is the MORE
# important of the two to keep polite.
#
# `ninja -j`, not `autoninja`: autoninja recomputes its own job count from the
# machine and silently overrides the cap. Observed once: a run that was
# supposed to hold 24 of 64 cores spawned 54 concurrent clang processes and
# drove CPU pressure to 85%.
NPROC="$(nproc)"
JOBS="${CAMOUWOLF_NINJA_JOBS:-$NPROC}"
NICE=()
if [ "${CAMOUWOLF_NICE:-0}" = 1 ]; then
  NICE=(nice -n 19)
  command -v ionice >/dev/null && NICE+=(ionice -c3)
fi

echo "==> ninja (${JOBS}/${NPROC} jobs${NICE:+, niced})"
"${NICE[@]}" ninja -C "out/$OUT_NAME" -j "$JOBS" chrome

[ "$TARGET" = linux ] || { echo "==> built $OUT ($TARGET $ARCH)"; exit 0; }
echo "==> $OUT/chrome"
"$OUT/chrome" --version
