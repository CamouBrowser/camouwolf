#!/usr/bin/env bash
# Full Camouwolf browser build: depot_tools, sync to the pinned tag, apply the patch
# series, configure, compile.
#
# Usage:  bash browser/scripts/build.sh [workdir]
#   workdir defaults to $CAMOUWOLF_BUILD_DIR or ./.camouwolf-build
#
# Needs ~100 GB free and, cold, several hours. Warm rebuilds after a patch edit
# are ~10-20 min (see scripts/build-camouwolf-incremental.sh).
#
# Env:
#   CHROMIUM_VERSION  override the pin in browser/upstream.sh
#   CAMOUWOLF_BUILD_DIR   where the checkout lives
#   TMPDIR            point at real disk; a tmpfs /tmp WILL fill and fail the link
#
# Resource caps — for building on a box that is doing something else:
#   CAMOUWOLF_NINJA_JOBS  cap ninja parallelism (default: all cores)
#   CAMOUWOLF_NICE=1      run the compile under `nice -n 19` + `ionice -c3`
#   CAMOUWOLF_MIN_FREE_GB abort if free space on the work volume drops below this
#                     (default 40). A Chromium link that runs the disk to zero
#                     takes the whole machine's other tenants down with it, so
#                     this is checked continuously, not just at startup.
#
# Network:
#   CAMOUWOLF_INSTALL_DEPS=1  run Chromium's install-build-deps.sh (uses sudo)
#   CAMOUWOLF_RATE_LIMIT_MB  cap the whole sync's download speed, in MB/s.
#                     gclient, git, cipd and gsutil have no limit of their own;
#                     this routes them through browser/scripts/ratecap-proxy.py.
#
# Stages:
#   CAMOUWOLF_STAGES  space-separated subset of "sync patch build" (default:
#                     all three). "sync" alone prepares a checkout while the
#                     patch series is still being worked on.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/browser/upstream.sh"

WORK="${1:-${CAMOUWOLF_BUILD_DIR:-$REPO/.camouwolf-build}}"
VER="${CHROMIUM_VERSION:-$version}"
OUT="Camouwolf"
export TMPDIR="${TMPDIR:-$WORK/tmp}"
mkdir -p "$WORK" "$TMPDIR"

# ------------------------------------------------------------- resource caps
NPROC="$(nproc)"
JOBS="${CAMOUWOLF_NINJA_JOBS:-$NPROC}"
MIN_FREE_GB="${CAMOUWOLF_MIN_FREE_GB:-40}"

# nice/ionice are applied to the COMPILE only, not to the sync. `gclient sync`
# is network-bound and already polite; niceing it just makes the slowest phase
# slower without freeing anything a co-tenant wants.
NICE=()
if [ "${CAMOUWOLF_NICE:-0}" = 1 ]; then
  NICE=(nice -n 19)
  command -v ionice >/dev/null && NICE+=(ionice -c3)
fi

free_gb() { df -BG --output=avail "$WORK" | tail -1 | tr -dc '0-9'; }

# The startup check. Cold, a Chromium checkout plus an official build lands
# around 100-120 GB, so refuse rather than discover it three hours in.
AVAIL="$(free_gb)"
if [ "$AVAIL" -lt 130 ]; then
  echo "!! only ${AVAIL}G free on $(df --output=target "$WORK" | tail -1)." >&2
  echo "   A cold Chromium checkout + official build needs ~120G." >&2
  echo "   Point CAMOUWOLF_BUILD_DIR at a bigger volume." >&2
  exit 1
fi

echo "==> Camouwolf $release | Chromium $VER | out/$OUT | $WORK"
echo "    ${JOBS}/${NPROC} jobs${NICE:+ | niced}| ${AVAIL}G free | abort below ${MIN_FREE_GB}G"

# ----------------------------------------------------------- disk watchdog
# Runs for the life of the build. ninja will happily fill a disk and then fail
# with a link error that says nothing about space; on a shared box it takes the
# other tenants down first. This kills the build while there is still room to
# clean up, and says why.
watchdog() {
  while :; do
    sleep 60
    local now; now="$(free_gb)"
    if [ "$now" -lt "$MIN_FREE_GB" ]; then
      echo "!! DISK WATCHDOG: ${now}G free (< ${MIN_FREE_GB}G) — killing the build." >&2
      pkill -P $$ -f 'ninja|autoninja|clang' 2>/dev/null
      kill -TERM $$ 2>/dev/null
      return
    fi
  done
}
watchdog &
WATCHDOG_PID=$!
trap 'kill "$WATCHDOG_PID" 2>/dev/null || true' EXIT

STAGES=" ${CAMOUWOLF_STAGES:-sync patch build} "
stage() { case "$STAGES" in *" $1 "*) return 0 ;; esac; return 1; }

# ----------------------------------------------------------------- rate limit
if [ -n "${CAMOUWOLF_RATE_LIMIT_MB:-}" ] && stage sync; then
  PORT_FILE="$TMPDIR/ratecap-port.$$"
  rm -f "$PORT_FILE"
  python3 "$REPO/browser/scripts/ratecap-proxy.py" \
    --mb-per-s "$CAMOUWOLF_RATE_LIMIT_MB" --port-file "$PORT_FILE" &
  RATECAP_PID=$!
  trap 'kill "$WATCHDOG_PID" "$RATECAP_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 50); do [ -s "$PORT_FILE" ] && break; sleep 0.1; done
  [ -s "$PORT_FILE" ] || { echo "!! the rate-limit proxy did not start" >&2; exit 1; }
  export https_proxy="http://127.0.0.1:$(cat "$PORT_FILE")"
  export http_proxy="$https_proxy" HTTPS_PROXY="$https_proxy" HTTP_PROXY="$https_proxy"
  export no_proxy="localhost,127.0.0.1" NO_PROXY="localhost,127.0.0.1"
  echo "    downloads capped at ${CAMOUWOLF_RATE_LIMIT_MB} MB/s"
fi

# ---------------------------------------------------------------- depot_tools
if [ ! -d "$WORK/depot_tools" ]; then
  git clone --depth 1 \
    https://chromium.googlesource.com/chromium/tools/depot_tools.git \
    "$WORK/depot_tools"
fi
export PATH="$WORK/depot_tools:$PATH"

# depot_tools vendors its own Python and CIPD binaries, and several of its
# wrappers — `gn` among them — refuse to run until that has happened, with:
#
#   python3_bin_reldir.txt not found. need to initialize depot_tools by
#   running gclient, update_depot_tools or ensure_bootstrap.
#
# DEPOT_TOOLS_UPDATE=0 suppresses exactly the step that creates it, so setting
# the pin before the first bootstrap gets you a sync that works (gclient falls
# back to system python) followed by a `gn gen` that dies — after the sync, the
# dep install, and the patches, which is a maximally annoying place to fail.
#
# Bootstrap explicitly, then pin. The pin is worth keeping: an auto-updating
# toolchain means two builds of the same Chromium tag can use different
# depot_tools, which is the opposite of what a reproducible build wants.
if [ ! -f "$WORK/depot_tools/python3_bin_reldir.txt" ]; then
  echo "--> bootstrapping depot_tools (one-time)"
  ( cd "$WORK/depot_tools" && ./ensure_bootstrap )
fi
export DEPOT_TOOLS_UPDATE=0

# ------------------------------------------------------------- fetch and sync
if [ ! -d "$WORK/chromium/src" ]; then
  mkdir -p "$WORK/chromium"
  ( cd "$WORK/chromium" && fetch --nohooks --no-history chromium )
fi
cd "$WORK/chromium/src"

if stage sync; then

# `git checkout -f` before syncing: it also discards a previous run's applied
# patches, which is what makes a rebuild reproducible rather than cumulative.
git fetch --depth 1 origin "refs/tags/$VER:refs/tags/$VER"
git checkout -f "tags/$VER"
rm -f .camouwolf-patched

# `-j` capped deliberately. gclient defaults to one job per core, and Chromium's
# DEPS has ~350 entries — on a many-core box that means hundreds of concurrent
# git fetches against chromium.googlesource.com. That gets the host rate-limited:
# the first jobswipe run died on `HTTP 429` at third_party/neon_2_sse after ~40
# minutes and 43 GB, having logged 19 backoff retries first. A lower parallelism
# is both faster (no backoff tail) and kinder to a shared host's network budget.
#
# The retry loop exists because a 429 is TRANSIENT and a sync failure otherwise
# throws away the whole run. gclient sync is incremental — already-fetched repos
# are kept — so each attempt resumes rather than restarting, and the backoff
# halves parallelism each time to climb back under whatever limit was tripped.
SYNC_JOBS="${CAMOUWOLF_SYNC_JOBS:-16}"
SYNC_OK=0
for attempt in 1 2 3 4; do
  echo "--> gclient sync (attempt $attempt, -j $SYNC_JOBS)"
  if gclient sync -D --no-history --with_branch_heads --reset -j "$SYNC_JOBS"; then
    SYNC_OK=1
    break
  fi
  if [ "$attempt" -lt 4 ]; then
    SYNC_JOBS=$(( SYNC_JOBS / 2 )); [ "$SYNC_JOBS" -lt 1 ] && SYNC_JOBS=1
    BACKOFF=$(( attempt * 120 ))
    echo "!! sync failed — retrying in ${BACKOFF}s at -j ${SYNC_JOBS}" >&2
    echo "   (a 429 from googlesource is the usual cause and clears on its own)" >&2
    sleep "$BACKOFF"
  fi
done
if [ "$SYNC_OK" != 1 ]; then
  echo "!! gclient sync failed after 4 attempts — not a transient throttle." >&2
  exit 1
fi

# Installs system packages with sudo, so it is opt-in: a build box that serves
# other work should not have its packages changed by a browser build.
if [ "${CAMOUWOLF_INSTALL_DEPS:-0}" = 1 ]; then
  ./build/install-build-deps.sh --no-prompt
fi
fi  # sync

# ------------------------------------------------------------------- patch it
stage patch && bash "$REPO/browser/scripts/apply-patches.sh" "$WORK/chromium/src" --force

# ------------------------------------------------------------ configure, build
stage build || { echo "==> stages done:$STAGES"; exit 0; }
gn gen "out/$OUT" --args="$(cat "$REPO/browser/args.gn")"

# `ninja -j`, not `autoninja`. autoninja recomputes its own job count from the
# machine (and from reclient/goma when configured), which silently overrides the
# cap — the whole point of CAMOUWOLF_NINJA_JOBS on a shared box.
"${NICE[@]}" ninja -C "out/$OUT" -j "$JOBS" chrome chrome_crashpad_handler

BIN="$WORK/chromium/src/out/$OUT/chrome"
echo "==> built: $BIN"
"$BIN" --version

# A build that reports its own version but cannot start under CAMOU_CONFIG is
# worse than one that fails to link, because it looks fine. Smoke it.
echo "==> config smoke test"
CAMOU_CONFIG_1='{"navigator.platform":"Win32"}' \
  "$BIN" --headless=new --disable-gpu --dump-dom about:blank >/dev/null \
  && echo "    ok" \
  || { echo "    FAILED to start with CAMOU_CONFIG set" >&2; exit 1; }
