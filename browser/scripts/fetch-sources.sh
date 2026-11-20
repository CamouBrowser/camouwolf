#!/usr/bin/env bash
# Fetch the exact upstream Chromium files Camouwolf patches, at the pinned tag, into
# a scratch mirror tree. This is the patch-AUTHORING rig, not the build: a real
# build syncs the whole tree with depot_tools (see browser/scripts/build.sh).
#
# Why it exists: a patch is only as good as its context lines. Authoring against
# a mirror of the exact tag means `git apply` on the real checkout either applies
# cleanly or fails loudly — never lands a hunk in the wrong place, which is the
# failure mode Camoufox's patch-assertions were built to catch.
#
# Usage:  bash browser/scripts/fetch-sources.sh [dest]
#           dest defaults to browser/.pristine/<version>
#         CAMOUWOLF_FILELIST overrides the file list (default: browser/scripts/patched-files.txt)
#         CAMOUWOLF_SOURCE_BASE overrides the googlesource URL (the self-tests serve one)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/browser/upstream.sh"

DEST="${1:-$REPO/browser/.pristine/$version}"
FILELIST="${CAMOUWOLF_FILELIST:-$REPO/browser/scripts/patched-files.txt}"
BASE="${CAMOUWOLF_SOURCE_BASE:-https://chromium.googlesource.com/chromium/src/+/refs/tags/$version}"

mkdir -p "$DEST"
echo "==> mirroring Chromium $version -> $DEST"

fail=0
while read -r path; do
  case "$path" in ''|\#*) continue ;; esac
  out="$DEST/$path"
  if [ -s "$out" ]; then
    echo "    cached  $path"
    continue
  fi
  mkdir -p "$(dirname "$out")"
  # googlesource serves base64 with ?format=TEXT; it is the only stable way to
  # get raw bytes without cloning 100GB.
  # Only a 404 means the file is gone. googlesource throttles shared CI
  # runners; curl retries its 429/5xx with backoff and Retry-After, as
  # check-patch-apis.py does.
  code=$(curl -sS --retry 6 --retry-max-time 300 -o "$out.b64" -w '%{http_code}' "$BASE/$path?format=TEXT" || true)
  if [ "$code" = 200 ] && base64 -d < "$out.b64" > "$out" 2>/dev/null && [ -s "$out" ]; then
    echo "    fetched $path ($(wc -l < "$out") lines)"
    rm -f "$out.b64"
    continue
  elif [ "$code" = 404 ]; then
    echo "    MISSING $path — file moved or renamed upstream" >&2
    fail=1
  else
    echo "    FAILED  $path — googlesource answered ${code:-nothing}" >&2
    fail=1
  fi
  rm -f "$out" "$out.b64"
done < "$FILELIST"

if [ "$fail" != 0 ]; then
  echo "==> some files could not be fetched: reconcile browser/scripts/patched-files.txt for MISSING ones" >&2
  exit 1
fi
echo "==> ok"
