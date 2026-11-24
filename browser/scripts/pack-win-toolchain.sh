#!/usr/bin/env bash
# Packs the Visual Studio + Windows SDK toolchain zip that a Linux host needs to
# cross-compile Chromium for Windows (Chromium's docs/win_cross.md). Runs on a
# Windows machine with Visual Studio 2026 and its ARM64 and ATL/MFC components,
# from Git Bash. The zip is Microsoft's licensed software: never publish it.
#
# Usage:  bash browser/scripts/pack-win-toolchain.sh <out dir>
# Prints the zip's hash, which the Linux side passes as GYP_MSVS_HASH_<pin>.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO/browser/upstream.sh"
OUT="$(mkdir -p "${1:?out dir}" && cd "$1" && pwd)"
export MSYS_NO_PATHCONV=1   # Git Bash would rewrite the installer's /switches as paths

# The SDK this Chromium tag requires (its build/vs_toolchain.py), and the
# installer for it. A new pin that moves the SDK fails here, not mid-build.
SDK_VERSION=10.0.28000.0
SDK_INSTALLER="https://go.microsoft.com/fwlink/?linkid=2370315"   # 10.0.28000.2270
PINNED="$(curl -fsSL "https://chromium.googlesource.com/chromium/src/+/refs/tags/$version/build/vs_toolchain.py?format=TEXT" \
  | base64 -d | sed -nE "s/^SDK_VERSION = '(.*)'/\1/p")"
[ "$PINNED" = "$SDK_VERSION" ] || { echo "!! Chromium $version wants Windows SDK $PINNED; this script installs $SDK_VERSION" >&2; exit 1; }

WORK="$(mktemp -d)"
cd "$WORK"
if [ ! -d "/c/Program Files (x86)/Windows Kits/10/Include/$SDK_VERSION" ]; then
  curl -fsSL -o winsdksetup.exe "$SDK_INSTALLER"
  ./winsdksetup.exe /features + /quiet /norestart /ceip off
fi
# package_from_installed.py refuses an SDK without its debuggers (dbghelp.dll).
[ -d "/c/Program Files (x86)/Windows Kits/10/Debuggers/x64" ] || { echo "!! the Windows SDK debuggers are not installed" >&2; exit 1; }

git clone --depth 1 https://chromium.googlesource.com/chromium/tools/depot_tools.git
cd "$OUT"
python "$WORK/depot_tools/win_toolchain/package_from_installed.py" 2026 -w "$SDK_VERSION"
ls *.zip | sed 's/\.zip$//'
