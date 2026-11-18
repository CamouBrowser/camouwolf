#!/usr/bin/env bash
# =============================================================================
# Camouwolf HEAVY gate — build the browser from the CURRENT tree, then judge the
# freshly built binary against the full leak suite. Non-zero on any leak.
# This is the enforced gate for PRs into `main` that touch browser/patches/.
#
# WHY IT BUILDS FIRST, ALWAYS:
#   A prebuilt tree is not evidence. Holo lost a release to a patch that was
#   "validated" against a stale binary and reported a false 750/750, and the
#   same shape is available here — a patch whose config key no code reads
#   produces a build that starts, passes a smoke test, and spoofs nothing. This
#   script builds so the binary under test contains the PR's patches by
#   construction.
#
# THE ORACLE IS SUNDIAL (daijro/sundial, run locally). We do not reinvent
# fingerprint checks: sundial's ~760 tests and its self-contained detectors ARE
# the suite. See tests/compare/sundial-drive.mjs.
#
# Usage:  bash tests/ci/run-heavy-suite.sh
# Env:
#   CI_STAGES     subset, default "patches build surface cdp sundial playwright"
#   CI_SKIP_BUILD 1 to judge an existing build (LOCAL ONLY — CI never sets it)
#   SUNDIAL_DIR   the sundial checkout; searched if unset
#   CAMOUWOLF_GPU     disambiguate a multi-GPU box for virtual-gpu probes
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUITE="$ROOT/tests"
STAGES="${CI_STAGES:-patches build surface cdp sundial playwright}"
SUNDIAL_URL="${SUNDIAL_URL:-http://localhost:5173}"
BUILD_DIR="${CAMOUWOLF_BUILD_DIR:-$ROOT/.camouwolf-build}"

# Locate the sundial oracle. Searched rather than hardcoded to one checkout: the
# gate runs on more than one machine and the repo does not live in the same
# place on each. A wrong hardcoded path fails the stage HOURS into a run, after
# the build, which is the most expensive moment to discover a missing
# prerequisite.
if [ -z "${SUNDIAL_DIR:-}" ]; then
  for candidate in \
    "$HOME/jobharvest/sundial" \
    "$HOME/sundial" \
    "$(dirname "$ROOT")/sundial" \
    /data/sundial; do
    [ -d "$candidate" ] && { SUNDIAL_DIR="$candidate"; break; }
  done
fi
SUNDIAL_DIR="${SUNDIAL_DIR:-}"

# This box has two GPUs and the launcher refuses to guess: virtual-gpu throws
# AmbiguousGpuSelection rather than silently picking one. Exported ONCE so every
# probe stage inherits it — Holo's gate once had this set per-stage, which meant
# one stage died before launching and its empty result was read as a leak.
export CAMOUWOLF_GPU="${CAMOUWOLF_GPU:-128}"

# shellcheck source=/dev/null
source "$ROOT/browser/upstream.sh"

FAILED=()
stage_ran() { [[ " $STAGES " == *" $1 "* ]]; }
banner() { printf '\n=== %s %s\n' "$1" "$(printf '%.0s-' {1..50})"; }
fail() { echo "  !! $1"; FAILED+=("$1"); }

# ------------------------------------------------------------- 0. pre-flight
# Every prerequisite the LATER stages need, checked before the build burns
# hours. Each of these has actually bitten: a box whose default `node` is 20
# (the launcher hits an ESM crash there that presents as a browser bug), a
# missing pnpm, no Xvfb for the Playwright stage, no sundial checkout. All of
# them surface after the build otherwise, which is the single most expensive
# moment to learn you were missing a package.
banner "pre-flight"
PREFLIGHT_BAD=0
require() {
  if command -v "$1" >/dev/null 2>&1; then
    printf '  %-12s ok\n' "$1"
  else
    printf '  %-12s MISSING — %s\n' "$1" "$2"
    PREFLIGHT_BAD=1
  fi
}
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -ge 22 ]; then
  printf '  %-12s ok (v%s)\n' "node" "$(node -p 'process.versions.node')"
else
  printf '  %-12s v%s — Camouwolf needs >= 22 (Node 20 hits an ESM crash that\n' \
    "node" "$(node -p 'process.versions.node' 2>/dev/null || echo '?')"
  printf '               %s\n' "looks like a browser bug). nvm use 22."
  PREFLIGHT_BAD=1
fi
require pnpm   "corepack enable && corepack prepare pnpm@10.33.4 --activate"
require python3 "needed by check-dead-patches.py"
require git    "needed by depot_tools"
stage_ran playwright && require xvfb-run "apt install xvfb"
if stage_ran sundial; then
  if [ -n "$SUNDIAL_DIR" ] && [ -d "$SUNDIAL_DIR" ]; then
    printf '  %-12s ok (%s)\n' "sundial" "$SUNDIAL_DIR"
  else
    printf '  %-12s MISSING — set SUNDIAL_DIR\n' "sundial"
    PREFLIGHT_BAD=1
  fi
fi
if [ "$PREFLIGHT_BAD" != 0 ]; then
  echo "!! pre-flight failed — fix the above before spending hours on a build." >&2
  exit 1
fi

# ---------------------------------------------------------------- 1. patches
# Cheapest stage, so it runs first: a DEAD or CORRUPT patch after a Chromium
# bump makes every later stage's result meaningless.
if stage_ran patches; then
  banner "patches"
  if ! python3 "$ROOT/scripts/check-dead-patches.py"; then
    fail "patch series does not apply cleanly to Chromium $version"
    echo "!! stopping — later stages would judge a partially-patched build."
    exit 1
  fi
  # Applying is not compiling. This catches renamed/forbidden APIs and bad
  # include paths inside the hunks — the failures that otherwise surface hours
  # later, in the middle of the build.
  API_ARGS=()
  [ -d "$BUILD_DIR/chromium/src" ] && API_ARGS=(--src "$BUILD_DIR/chromium/src")
  if ! python3 "$ROOT/scripts/check-patch-apis.py" "${API_ARGS[@]}"; then
    fail "patch series uses APIs that will not compile"
    echo "!! stopping — fix these before spending hours on a build."
    exit 1
  fi
  # The launcher and the browser are joined by a string, and nothing else
  # checks that join. A key written into CAMOU_CONFIG that no accessor reads is
  # an identity the launcher believes it applied and did not.
  if ! python3 "$ROOT/scripts/check-config-keys.py"; then
    fail "a config key the launcher writes has no reader"
    exit 1
  fi
  # Cheap, and the failure it catches is invisible downstream: a persona whose
  # fontconfig does not parse renders with the BUILD MACHINE's fonts, which
  # looks like a pass everywhere except a font probe.
  if ! python3 "$ROOT/scripts/check-bundle-fontconfig.py"; then
    fail "bundled fontconfig personas are broken"
    exit 1
  fi
fi

# ----------------------------------------------------------------- 2. build
EXE="$BUILD_DIR/chromium/src/out/Camouwolf/chrome"
if stage_ran build; then
  banner "build"
  if [ "${CI_SKIP_BUILD:-0}" = 1 ]; then
    echo "  CI_SKIP_BUILD=1 — reusing $EXE (local only)"
  else
    if ! bash "$ROOT/browser/scripts/build.sh" "$BUILD_DIR"; then
      fail "browser build"
      exit 1
    fi
  fi
fi
if [ ! -x "$EXE" ]; then
  echo "!! no binary at $EXE" >&2
  exit 1
fi
export CAMOUWOLF_EXECUTABLE_PATH="$EXE"
echo "  binary: $EXE"

# The probes import the BUILT launcher (dist/index.js). Build it once, here,
# rather than in whichever stage happens to run first.
banner "launcher"
( cd "$ROOT" && pnpm install --frozen-lockfile && pnpm run build ) || {
  fail "launcher build"; exit 1;
}

# --------------------------------------------------------------- 3. surface
# Does the browser actually report what the launcher claimed? This is the stage
# that catches a config key no patch reads.
if stage_ran surface; then
  banner "surface"
  for os in windows linux; do
    if ! node "$SUITE/compare/stealth-surface.mjs" --os "$os" --seed 1; then
      fail "stealth-surface ($os)"
    fi
  done
fi

# ------------------------------------------------------------------- 4. cdp
if stage_ran cdp; then
  banner "cdp"
  node "$SUITE/compare/cdp-leak.mjs" || fail "cdp-leak"
fi

# --------------------------------------------------------------- 5. sundial
if stage_ran sundial; then
  banner "sundial"
  if [ -z "$SUNDIAL_DIR" ] || [ ! -d "$SUNDIAL_DIR" ]; then
    fail "sundial oracle not found — set SUNDIAL_DIR (searched \$HOME/jobharvest/sundial, \$HOME/sundial, $(dirname "$ROOT")/sundial, /data/sundial)"
  else
    # Start the dev server, wait for it, and always tear it down.
    ( cd "$SUNDIAL_DIR" && npm run dev -- --port 5173 >/tmp/camouwolf-sundial.log 2>&1 ) &
    SUNDIAL_PID=$!
    trap 'kill $SUNDIAL_PID 2>/dev/null' EXIT
    for _ in $(seq 1 60); do
      curl -sf "$SUNDIAL_URL" >/dev/null 2>&1 && break
      sleep 1
    done
    if ! curl -sf "$SUNDIAL_URL" >/dev/null 2>&1; then
      fail "sundial dev server did not come up (see /tmp/camouwolf-sundial.log)"
    else
      for os in windows linux; do
        node "$SUITE/compare/sundial-drive.mjs" \
          --url "$SUNDIAL_URL" --os "$os" \
          --out "/tmp/camouwolf-sundial-$os.json" || fail "sundial ($os)"
      done
    fi
    kill $SUNDIAL_PID 2>/dev/null
    trap - EXIT
  fi
fi

# ------------------------------------------------------------ 6. playwright
# The functionality half. A stealth build that fails the upstream suite is
# useless no matter how it scores on a detector — and every patch is a chance
# to break ordinary web behavior.
if stage_ran playwright; then
  banner "playwright"
  xvfb-run -a bash "$SUITE/launcher/run-tests.sh" \
    --upstream-only --executable-path "$EXE" || fail "upstream playwright suite"
fi

# ------------------------------------------------------------------ verdict
banner "verdict"
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "HEAVY SUITE: PASS"
  exit 0
fi
echo "HEAVY SUITE: FAIL"
printf '  - %s\n' "${FAILED[@]}"
exit 1
