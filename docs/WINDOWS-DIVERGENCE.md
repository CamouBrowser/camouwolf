# Real Windows Chrome vs Camouwolf-on-Linux: measured divergences

A direct A/B of every fingerprint surface a page can read, measured on real
Google Chrome on Windows 11 and on stock Chromium on this Linux box, both
windowed on a real display.

This is a record of one measurement, taken against the Chromium 152 build the
patch series was ported from. The probe and the capture files it produced were
not carried into this repository; the numbers below are what they showed. The
section numbers are cited from `browser/`, so they are kept as they were.

## What was measured, and what was not

| Leg | Browser | Where |
| --- | --- | --- |
| **A — real Windows** | Google Chrome **151.0.7922.169** | Win 11 Home 26200, i9-11900H (16t), Intel UHD via **D3D11**, 1383x864 @2.5 |
| **B — stock Linux** | Chrome for Testing **151.0.7922.34** | this box, RTX 5080 via **ANGLE/OpenGL ES**, 1920x1080 @1 |
| **C — Camouwolf's claim** | — | the `CAMOU_CONFIG` the launcher builds for `--os windows --seed 1` |

Both browsers are milestone 151 on the same `7922` branch, so version skew is
nearly zero and essentially every difference is OS, GPU or branding.

**The fourth leg — a real Camouwolf binary — could not be run: there is no build on
this machine** (no `browser-cache/`, no `.camouwolf-build/`, no
`CAMOUWOLF_EXECUTABLE_PATH`, no Chromium checkout, and `browser/scripts/build.sh` is
a multi-hour ~100 GB job). So this report proves what Camouwolf *intends* to report
(leg C, from the config it actually builds) and what it *starts from* (leg B),
but every claim about what a running Camouwolf emits is marked **[needs binary]**.

Two legs are driven over a CDP + reverse-HTTP tunnel so the Windows browser
stays windowed on its own desktop and still fetches the probe page from
`127.0.0.1` — same secure context on both sides.

## 1. A patch that creates the tell it was written to remove

**`0022-system-ui-font` spoofs the font family but not the size.**

Measured, `getComputedStyle` after `font: <keyword>`:

| keyword | real Windows | stock Linux |
| --- | --- | --- |
| `caption`, `icon`, `message-box` | Arial **16px** | Arial 16px |
| `menu`, `small-caption`, `status-bar` | **Segoe UI 12px** | Arial 16px |

The patch's split — Segoe UI for exactly those three, Arial for the rest — is
confirmed correct. But it only overrides `LayoutThemeFontProvider::
SystemFontFamily`; `SystemFontSize` in
`layout_theme_font_provider_default.cc` returns `DefaultFontSize()` = 16px for
every keyword. Windows' own provider returns the NONCLIENTMETRICS sizes.

So a Camouwolf Windows persona reports **`Segoe UI` at `16px`** — a pairing no real
Windows machine produces, and one that only appears *because* the patch ran.
Fix: override `SystemFontSize` for the same three keywords.

Since then 0022 overrides `SystemFontSize` for the same three keywords, so they
report Segoe UI at 12px.

## 2. Confirmed unspoofed divergences

### 2.1 Canvas text: the first read differs on Linux only

Draw text to a canvas, then `getImageData` twice:

| | read 1 vs read 2 | reads 2–8 |
| --- | --- | --- |
| real Windows | identical | identical |
| stock Linux | **differ** | identical |

Isolated further: a geometry-only canvas (no text) is stable from the first read
on **both**; only the text canvas diverges, and `await document.fonts.load(...)`
+ `document.fonts.ready` does **not** fix it.

Since measured against a build: it is not glyph-cache warm-up. The first read
of an accelerated canvas comes from the GPU, which unpremultiplied in a shader;
the later reads come from a CPU copy, which rounds low-alpha pixels (the edges
of antialiased text) differently. Only those pixels differed, and a CPU canvas
was stable. Fixed by 0037; regression test `tests/native/test_canvas_readback.py`.

This is a three-line probe that identifies Linux regardless of every other
spoof, and **canvas farbling cannot mask it**: `0009` perturbs both reads with
the same seed, so the underlying instability passes straight through.

### 2.2 WebGL — the backend signature contradicts the claimed renderer

Camouwolf claims `ANGLE (NVIDIA, ... Direct3D11 vs_5_0 ps_5_0, D3D11)`. These are
**D3D11-vs-OpenGL backend signatures**, not GPU-specific values, so they hold
regardless of which card either box has:

| parameter | real Windows (D3D11) | Linux (GL) | spoofed? |
| --- | --- | --- | --- |
| `ALIASED_LINE_WIDTH_RANGE` | `[1,1]` | `[1,10]` | no |
| `ALIASED_POINT_SIZE_RANGE` | `[1,1024]` | `[1,2047]` | no |
| `MAX_VIEWPORT_DIMS` | `[32767,32767]` | `[32768,32768]` | no |
| `shaderPrecisionFormats` LOW/MEDIUM_FLOAT | `[127,127,23]` | `[15,15,10]` | no |

`ALIASED_LINE_WIDTH_RANGE = [1,1]` and `MAX_VIEWPORT_DIMS = 32767` are D3D11
constants; a browser claiming a D3D11 renderer while reporting the GL values is
self-contradicting on a single `getParameter` call.

Extension lists also disagree, in both directions:

- Linux-only: `WEBGL_compressed_texture_astc`, `_etc`, `_etc1`
- Windows-only: `WEBGL_draw_buffers` (WebGL1), `OVR_multiview2`,
  `WEBGL_provoking_vertex` (WebGL2)

Of the numeric parameters, **20 differ in WebGL2** and Camouwolf spoofs **10**
(`webGl:parameters`). Those 10 are correct — see §3.

Leg A is a complete real-Windows-D3D11 record, which is what the WebGL backend
signatures needed.

Since then the launcher sends the backend signature with the renderer: the
fingerprint's own WebGL1 and WebGL2 limits, precision table and extension list,
whenever its readout names the claimed renderer (0007). The Linux-only
extensions are hidden; the Windows-only ones cannot be added where the host
lacks them.

### 2.3 WebGPU

| field | real Windows | Linux | spoofed? |
| --- | --- | --- | --- |
| `preferredCanvasFormat` | `bgra8unorm` | `rgba8unorm` | **no** |
| `info.vendor` / `architecture` | intel / gen-12lp | nvidia / blackwell | yes |
| `subgroupMinSize` / `MaxSize` | 8 / 16 | 32 / 32 | no |
| `limits.maxBufferSize` | 2147483648 | 4294967292 | no |
| `limits.maxComputeWorkgroupStorageSize` | 32768 | 49152 | no |
| feature `shader-f16` | present | absent | no |

`navigator.gpu.getPreferredCanvasFormat()` is a one-line, zero-permission OS
check and is not spoofed at all.

Since then every Windows and macOS identity claims `bgra8unorm`, and an
identity whose recorded adapter matches its renderer also claims that adapter's
subgroup sizes, features and limits (0008). Features stay hide-only and limits
tighten-only, so `shader-f16` and limits above the host's need a host GPU that
has them.

### 2.4 Media stack

| probe | real Windows | Linux |
| --- | --- | --- |
| `canPlayType('video/mp4; codecs="hev1..."')` | `probably` | `""` |
| `MediaSource.isTypeSupported(hev1/hvc1)` | `true` | `false` |
| `MediaRecorder.isTypeSupported('audio/mp4; mp4a.40.2')` | `true` | `false` |
| `decodingInfo(...).powerEfficient` | `true` | `false` |

HEVC support is a platform-decoder property. `0035` now answers all four from
the identity's recorded hardware codecs (`media:*` keys).

### 2.5 Patched surfaces the launcher never drives

`browser/` reads these keys; a `--os windows` launch writes none of them:

| key | patch | measured divergence |
| --- | --- | --- |
| `keyboard.layoutMap` | 0014 | `IntlBackslash` = `\` (Win) vs `<` (Lin) |
| `mediaDevices:enumerated` | 0013 | 3 devices (audioin/videoin/audioout) vs 2 |
| `connection.downlink` | 0015 | rtt 100/downlink 1.65 vs 50/1.5 — network-derived, not an OS difference (below) |
| `screen.availLeft` / `availTop` | 0004 | none here (both 0); needed for a side/top taskbar |
| `navigator.vendor` | 0002 | none — `Google Inc.` on both |
| `timezone` | 0006 | none here; only drawn on a geoip launch |

The first two were live leaks. Since then the launcher writes both for every
OS, from the recorded layouts and device lists.

`connection.rtt`/`downlink` stay unwritten on purpose. Chrome computes both
from the transfers it has timed, by the same code on every OS, so the two
samples above differ because the machines were on different networks. The
recorded fingerprints carry no connection values to draw from, and Chrome's own
estimate already describes the path a page's requests take, through the proxy
when there is one. A value a caller sets now goes through Chrome's per-host
noise and rounding (0015), as a measured one does.

### 2.6 Platform-gated APIs — closed by `0019`

Full enumeration of `window`/`navigator`/`document`/`screen` on a secure origin,
2540 vs 2504 reachable names. **Zero Linux-only** entries; Windows-only:

| API | covered by 0019? |
| --- | --- |
| `navigator.share` / `canShare` (Web Share) | **yes** |
| `navigator.bluetooth` + 8 `Bluetooth*` globals | **yes** |
| `window.EyeDropper` | already exposed on every identity at 158 |

Web Bluetooth answers as a machine without a Bluetooth adapter; see 0019 in
`docs/PATCHES.md`.

### 2.7 Window decoration geometry — stale

This measured **12 / 93** (`outerWidth − innerWidth` / `outerHeight − innerHeight`)
on a windowed Windows Chrome and **24 / 112** on Linux under a window manager.
The current build reports 0 / 87 for every identity, headless or headed, which
is the most common frame in the recorded Windows and macOS devices (a maximized
window). See "Window frame geometry" in `docs/PATCHES.md`.

### 2.8 Audio — one narrow region, and farbling is the wrong tool

The classic fingerprint (sum over samples 4500–5000 of the
oscillator→compressor render) is **bit-identical**: `124.04347527516074` on
both. Splitting the 44100-sample buffer into 45 chunks of 1000, **exactly one
chunk differs — samples 16000–16999**; the other 44 match exactly, as do the
first and last samples.

The residue is a ULP-scale
platform-math difference in a narrow window, not a per-device signal — so it is
the same *kind* of problem as `0018`, and per-identity noise (`0011`) would make
it strictly worse. `0011` is currently inert (`canvas:seed` defaults to 0);
leaving it off remains correct.

## 3. Verified correct

Checked against leg A and matching exactly:

- **System colors — 28/28.** Every colour that diverges between the two OSes has
  a claimed key, and every claimed value equals the real Windows value
  (`ActiveText`/`LinkText`/`VisitedText` `rgb(0,102,204)`, `ButtonFace`
  `rgb(240,240,240)`, `GrayText` `rgb(109,109,109)`, `Highlight`
  `rgb(0,120,215)`, `SelectedItem` `rgb(25,103,210)`, …).
- **WebGL parameters — 10/10.** `MAX_TEXTURE_SIZE` 16384, `MAX_VERTEX_ATTRIBS`
  16, `MAX_COMBINED_TEXTURE_IMAGE_UNITS` 32, `MAX_VARYING_VECTORS` 30,
  `MAX_VERTEX_UNIFORM_VECTORS` 4096 … all equal the real D3D11 values.
- **Taskbar inset.** Claimed `screen.height − availHeight` = 48; real Windows =
  48. Stock Linux = 0, which is the CreepJS `noTaskbar` tell.
- **Font model.** The Linux-looking families in a Windows persona (DejaVu,
  Liberation, Noto) come from the modelled `LIBREOFFICE6` addition group, which
  genuinely ships on Windows boxes with LibreOffice. The draw is
  all-or-nothing — 41/41 of that group, 0/6 of `PAN_EURO` — so it is coherent,
  not a partial leak.
- **Request headers.** Header name order is byte-identical between the two
  browsers, as are `Accept`, `Accept-Encoding` and `Accept-Language`.

Surfaces that need no spoofing because they already match: `Intl` (all of it),
`storage.estimate()`, `navigator.plugins`/`mimeTypes`, the whole `permissions`
matrix, error/stack shape and native `toString`s, `getBattery()` (on a host
with no battery; `0036` keeps a laptop host's from showing),
`performance.memory.jsHeapSizeLimit`, and **all 24 JS `Math.*` results
bit-for-bit** (V8 uses its own fdlibm, not the platform libm).

### 3.1 Where the C++ math does diverge — `0018` is justified

JS `Math.*` is identical, but the C++ geometry path is not:

| surface | result |
| --- | --- |
| `DOMMatrix.rotate/rotateAxisAngle/rotateFromVector/skew` | identical |
| computed CSS `transform` | identical |
| `getBoundingClientRect()` of a rotated element | **differs at 7 of 17 angles** (1°, 7.3°, 45°, 89.9999°, 135°, 1e5+0.5°, 1e15+1°) |
| `SVGMatrix.rotate(45)` | differs by 1 ULP |
| canvas `ctx.rotate(π/4)` | differs by 1 ULP |

The rotated-rect path is the loud one and is exactly what `0018`'s
`sin_cos_degrees.h` + `affine_transform.cc` target. Two notes: `dom_matrix.cc`
is also patched but that surface does **not** currently diverge, so the patch
must return values identical to what both platforms already agree on or it will
*create* a divergence [needs binary]; and whether `SVGMatrix`/canvas rotate
route through the patched `AffineTransform::Rotate` is likewise unverified.

## 4. Stale data

| item | Camouwolf | reality |
| --- | --- | --- |
| `Sec-CH-UA-Platform-Version` ladder | `["10.0.0","13.0.0","14.0.0","15.0.0"]` | Win 11 build 26200 reports **`19.0.0`** |
| UA Chrome version | `152.0.0.0` (pin `152.0.7977.42`, `release=alpha.1`) | stable is **151** |
| `deviceMemory` | presets emit `8` | Chrome 151 reported **32** on both boxes — the old cap of 8 has moved |

The ladder is the important one: it tops out at `15.0.0` (24H2), so **no Camouwolf
persona can claim a current Windows 11**, and seed 1 draws `10.0.0` — Windows
10, which is past end-of-support. The same stale ladder is quoted in
`CLAUDE.md`. Add `16.0.0`–`19.0.0`, verified against build 26200.

The UA version is a live-detectability issue *today*: a browser claiming Chrome
152 while stable is 151 is claiming a Dev/Canary build, which is rare traffic.

Since then the ladder ends at `19.0.0`, the pin is Chromium 158,
and `deviceMemory` is drawn from the buckets Chrome reports since it raised
the cap to 32 (`coherence.py`).

Branding: the Chromium build emits two brands (`Chromium` + GREASE); real Chrome
emits three, including `"Google Chrome"`. Camouwolf writes no `brands` key, so
whether `0003` synthesises the Chrome triple is **[needs binary]** — but a
Chromium-shaped `Sec-CH-UA` on a UA claiming Google Chrome would be a
first-request tell.

## 5. Not covered by this run

- Anything requiring the binary (see above).
- TLS/JA3/JA4 and HTTP/2 SETTINGS — the probe serves plain HTTP.
- Behavioural/timing signals; a single sample of `connection.*`.
- `maxTouchPoints`: the ground-truth box is a **touchscreen** laptop (10 points,
  `any-pointer: coarse`). Camouwolf claims 0 and cannot model a touch device
  (`navigator.maxTouchPoints` was in `KNOWN_DEAD`) — self-consistent, but it
  meant every Camouwolf Windows persona was a non-touch desktop. Since then
  0024 answers `maxTouchPoints` and the pointer media queries from the
  identity.

## 6. Harness artifacts, not findings

- `navigator.webdriver` reads `true` on leg B: Playwright launches with
  `--enable-automation`. Leg A attaches over CDP to an already-running browser
  and reads `false`. `0017` addresses this; the diff line is the harness.
- Leg B's GPU is an RTX 5080 and leg A's is an Intel UHD, so GPU-specific
  numbers differ for hardware reasons. §2.2 deliberately lists only
  backend-signature values, which are robust to that.
- Two probe bugs found and fixed mid-run, after which the affected numbers were
  re-derived: font presence required displacing *all three* generic fallbacks
  (a false negative for whichever family the platform maps a generic to — it
  reported Arial absent on Windows), and the voice probe waited for a single
  `voiceschanged` (it captured 19 of 22 voices, missing the local SAPI ones that
  arrive ~1s later).

## 7. Suggested order — done

Every item this report ordered has since been addressed: the `0022` font size,
the `platformVersion` ladder, the canvas first read (`0037`),
`keyboard.layoutMap` and `mediaDevices:enumerated`, the WebGL backend
signatures and `preferredCanvasFormat` (`0007`, `0008`), and Web Share and
Web Bluetooth (`0019`). What stays open is hardware the host lacks: WebGL
extensions and WebGPU features can be hidden but not added.
