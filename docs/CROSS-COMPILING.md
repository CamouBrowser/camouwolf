# Cross-compiling

Every Camouwolf target is built on an x86_64 Linux host, in CI and locally
(`ci/tribal-rules.yml`: `hosted-runners-only`). Chromium's own clang
and lld already emit and link PE and Mach-O for x86, x64 and arm64; what a Linux
host lacks is each vendor's SDK, which is licensed software and is never
committed or published.

```bash
CAMOUWOLF_TARGET=macos CAMOUWOLF_ARCH=arm64 CAMOUWOLF_MAC_SDK_DIR=<dir> \
CAMOUWOLF_OUT=Camouwolf-macos-arm64 bash browser/scripts/build.sh
bash scripts/release-camouwolf.sh --target macos --arch arm64 --skip-build --no-release
```

The GN args are `browser/args.gn` plus `browser/args/<os>.gn` and
`browser/args/<arch>.gn` (`browser/scripts/gn-args.sh`). The sync step writes
`target_os` and `target_cpu` into `.gclient`, so DEPS fetches the target's
toolchain hooks and sysroots on top of the host's.

## Status

A release builds the six targets camoufox releases (`ci/tribal-rules.yml`:
`releases-match-camoufox`); `ci/targets.py` lists them and `release.yml`'s
matrix follows it. A target is switched off there, with its reason, only while
its build is broken. Every target but linux x86_64 is first built by the
release run itself.

| Target | Series applies | `chrome` links | Runs on its OS |
|---|---|---|---|
| linux x86_64 | yes | yes | yes |
| linux arm64 | yes | not yet | not yet |
| windows x86_64 | yes | not yet | not yet |
| windows i686 | yes | not yet | not yet |
| macos x86_64 | yes | not yet | not yet |
| macos arm64 | yes | not yet | not yet |

## Linux arm64

Nothing beyond the sync: `target_cpu = ['arm64']` in `.gclient` brings in the
Debian arm64 sysroot.

## Windows

The Visual Studio and Windows SDK toolchain is a zip that Chromium's
`package_from_installed.py` makes from a Windows install
(`browser/scripts/pack-win-toolchain.sh`). The pinned Chromium wants Windows SDK
10.0.28000.0, which the hosted `windows-2025-vs2026` image does not carry, so the
script installs it first and fails if a new pin asks for another SDK. Point
`CAMOUWOLF_WIN_TOOLCHAIN_ZIP` at the `<hash>.zip`; build.sh maps the tag's
`TOOLCHAIN_HASH` to it (`GYP_MSVS_HASH_<pin>`) before the sync unpacks it. The zip carries the x86,
x64 and arm64 libraries, so one zip serves `x86_64` and `i686`
(`target_cpu = "x86"`, `browser/args/i686.gn`).

## macOS

`browser/scripts/pack-mac-sdk.sh` takes `Contents/version.plist` and the
MacOSX SDK from an installed Xcode whose SDK the pinned Chromium names in
`build/config/mac/mac_sdk.gni` (26.5 or 27.0 for Chromium 158). Point
`CAMOUWOLF_MAC_SDK_DIR` at the unpacked directory; build.sh links it in as
`build/mac_files/xcode_binaries`. Two args make the Linux host work:

- `use_system_xcode = false`: without it GN asks `xcrun` for the SDK.
- `enable_dsyms = false`: an official build makes dSYMs with Apple's
  `dsymutil`, which Chromium fetches only for a Mac host, and the release has
  no debug symbols to put in them.

The app is not signed with a Developer ID or notarized, as camoufox's is not.
The launchers download and unzip it themselves, so it carries no quarantine
attribute and Gatekeeper does not stop Playwright launching it; only a copy
downloaded with a web browser is quarantined.
