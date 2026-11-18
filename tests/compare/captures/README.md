# Ground-truth fingerprint captures

Reference measurements taken with `../capture-fingerprint.mjs`. They exist so a
divergence can be re-derived without standing up both machines again, and so a
future run can be diffed against a known-good baseline rather than against
memory.

| File | Browser | Machine |
| --- | --- | --- |
| `windows-real.json` | Google Chrome 151.0.7922.169 | Windows 11 Home 26200, i9-11900H, Intel UHD (D3D11), 1383x864 @2.5 |
| `linux-stock.json` | Chrome for Testing 151.0.7922.34 | Linux, RTX 5080 (ANGLE/OpenGL ES), 1920x1080 @1 |
| `camouwolf-claimed-seed1.json` | — | the `CAMOU_CONFIG` the launcher builds for `--os windows --seed 1` |
| `geo-*.json` | as above | the C++ geometry-math surface patch 0018 targets |
| `api-*.json` | as above | full enumerated API surface, captured on a secure origin |

Both browsers are milestone 151 on the same `7922` branch, so with two
exceptions noted in `../../../docs/WINDOWS-DIVERGENCE.md` every difference is an
OS, GPU or branding difference rather than a version difference.

Captured windowed on a real display on both sides — never headless, which
changes real values (see the `never-headless` rule).
