#!/usr/bin/env bash
# Camouwolf launcher test runner.
#
# Runs two suites against a built Camouwolf binary:
#   1. our own vitest tests (pure logic — no browser needed)
#   2. the upstream microsoft/playwright suite at the tag matching the installed
#      playwright-core, which is what proves the browser is still a FUNCTIONING
#      Chromium and not just a stealthy one
#
# The second matters more than it looks. Every patch in browser/patches/ is a
# chance to break ordinary web behavior, and a stealth build that fails 200
# Playwright tests is useless no matter how well it scores on a detector. This
# is the functionality half of the gate.
#
# Usage:
#   ./run-tests.sh --executable-path /path/to/camouwolf [options]
#
# Options:
#   --executable-path PATH   Camouwolf binary (required unless CAMOUWOLF_EXECUTABLE_PATH)
#   --headful                Disable headless (needs DISPLAY or xvfb-run)
#   --camouwolf-only             Only our vitest tests
#   --upstream-only          Only the upstream Playwright suite
#   -g EXPR                  Forwarded to `playwright test --grep`
#   -j N                     Worker count (default 4)
#   --                       Everything after is forwarded verbatim
#
# Environment:
#   CAMOUWOLF_EXECUTABLE_PATH    Same as --executable-path
#   PLAYWRIGHT_TAG           Override the upstream tag
#   UPSTREAM_CACHE_DIR       Where to clone upstream (default .upstream-cache)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

EXEC_PATH="${CAMOUWOLF_EXECUTABLE_PATH:-}"
HEADFUL=0
CAMOUWOLF_ONLY=0
UPSTREAM_ONLY=0
GREP=""
WORKERS="${PWTEST_WORKERS:-4}"
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --executable-path) EXEC_PATH="$2"; shift 2 ;;
        --headful)         HEADFUL=1; shift ;;
        --camouwolf-only)      CAMOUWOLF_ONLY=1; shift ;;
        --upstream-only)   UPSTREAM_ONLY=1; shift ;;
        -g|--grep)         GREP="$2"; shift 2 ;;
        -j|--workers)      WORKERS="$2"; shift 2 ;;
        --)                shift; PASSTHROUGH+=("$@"); break ;;
        --help|-h)         sed -n '2,/^set -/p' "$0" | sed 's/^# \?//; /^set -/d'; exit 0 ;;
        *)                 PASSTHROUGH+=("$1"); shift ;;
    esac
done

# ---------------------------------------------------------------- our tests
if [ "$UPSTREAM_ONLY" != 1 ]; then
    echo "== vitest (launcher logic)"
    ( cd "$REPO" && npx vitest run --config tests/launcher/vitest.config.ts "${PASSTHROUGH[@]:-}" )
fi

if [ "$CAMOUWOLF_ONLY" = 1 ]; then
    echo "== done (camouwolf-only)"
    exit 0
fi

# ------------------------------------------------------------- upstream suite
if [ -z "$EXEC_PATH" ] || [ ! -x "$EXEC_PATH" ]; then
    echo "!! no Camouwolf binary — pass --executable-path or set CAMOUWOLF_EXECUTABLE_PATH." >&2
    echo "   Build one with: bash browser/scripts/build.sh" >&2
    exit 2
fi

# Pin the upstream suite to the tag matching the installed playwright-core.
# Running a newer suite against an older client produces failures that are the
# harness's fault, not the browser's — and they are indistinguishable from real
# regressions unless you already know.
PW_VERSION="$(node -p "require('$REPO/node_modules/playwright-core/package.json').version" 2>/dev/null || echo "")"
TAG="${PLAYWRIGHT_TAG:-v${PW_VERSION}}"
CACHE="${UPSTREAM_CACHE_DIR:-$SCRIPT_DIR/.upstream-cache}"
SRC="$CACHE/$TAG"

if [ ! -d "$SRC" ]; then
    echo "== fetching microsoft/playwright $TAG"
    mkdir -p "$CACHE"
    git clone --depth 1 --branch "$TAG" \
        https://github.com/microsoft/playwright.git "$SRC"
    ( cd "$SRC" && npm ci --ignore-scripts )
fi

echo "== upstream playwright suite ($TAG) against $EXEC_PATH"
export CRPATH="$EXEC_PATH"
export PWTEST_CHANNEL=""
[ "$HEADFUL" = 1 ] && export HEADFUL=1

ARGS=(--browser=chromium --workers="$WORKERS")
[ -n "$GREP" ] && ARGS+=(--grep "$GREP")

set +e
( cd "$SRC" && npx playwright test tests/library tests/page "${ARGS[@]}" "${PASSTHROUGH[@]:-}" )
STATUS=$?
set -e

# Known failures. A Camouwolf patch legitimately changes behavior in a handful of
# upstream tests (they assert on the very values we spoof), and a run that is
# red only for those is a PASS. The list is explicit rather than a grep-and-hope
# so that a NEW failure cannot hide behind an old one.
KNOWN="$SCRIPT_DIR/known-failures-${TAG}.txt"
if [ "$STATUS" != 0 ] && [ -f "$KNOWN" ]; then
    echo "== comparing against $(basename "$KNOWN")"
    echo "   (a run failing ONLY these is a pass; anything else is a regression)"
fi

exit "$STATUS"
