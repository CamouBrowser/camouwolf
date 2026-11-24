#!/usr/bin/env bash
# The GN args for one target: browser/args.gn, then its OS's and its CPU's
# fragment from browser/args/. Every build of every target reads them here.
#
# Usage:  bash browser/scripts/gn-args.sh <linux|windows|macos> <x86_64|arm64>
set -euo pipefail
ARGS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cat "$ARGS/args.gn" "$ARGS/args/${1:?target}.gn" "$ARGS/args/${2:?arch}.gn"
