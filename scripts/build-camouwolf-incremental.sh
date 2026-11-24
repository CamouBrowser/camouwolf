#!/usr/bin/env bash
# Fast iteration on a warm Chromium tree: reset the patched files, re-apply the
# series, and let ninja rebuild only what changed (~10-20 min instead of hours).
#
# The whole point is that ninja's dependency graph survives. So this does NOT
# `gclient sync` and does NOT `gn gen` unless args.gn actually changed — either
# would invalidate the objdir and turn a 15-minute rebuild into a cold one.
#
# Usage:  bash scripts/build-camouwolf-incremental.sh
# Env:    CAMOUWOLF_BUILD_DIR (default .camouwolf-build)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/browser/upstream.sh"

BUILD_DIR="${CAMOUWOLF_BUILD_DIR:-$ROOT/.camouwolf-build}"
SRC="$BUILD_DIR/chromium/src"
OUT="$SRC/out/Camouwolf"

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
mapfile -t FILES < <(grep -hoP '(?<=^diff --git a/)\S+' "$ROOT"/browser/patches/*.patch | sort -u)
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

for f in "${FILES[@]}"; do
  git checkout -f -- "$f" 2>/dev/null || true
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

# gn gen only when args.gn moved. Re-running it needlessly rewrites build.ninja
# and can cascade into a much larger rebuild than the edit justified.
ARGS_STAMP="$OUT/.camouwolf-args-sha"
NEW_SHA="$(sha256sum "$ROOT/browser/args.gn" | cut -d' ' -f1)"
if [ ! -f "$ARGS_STAMP" ] || [ "$(cat "$ARGS_STAMP")" != "$NEW_SHA" ]; then
  echo "==> args.gn changed — regenerating"
  gn gen "out/Camouwolf" --args="$(cat "$ROOT/browser/args.gn")"
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
"${NICE[@]}" ninja -C "out/Camouwolf" -j "$JOBS" chrome

echo "==> $OUT/chrome"
"$OUT/chrome" --version
