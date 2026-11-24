# The patch series

45 diffs in `browser/patches/`, applied in `series` order against the Chromium
tag pinned in `browser/upstream.sh`.

Two rules govern all of them:

1. **Single surface per patch.** A patch that touches two unrelated surfaces
   cannot be reverted on its own when a Chromium bump breaks it — and a bump
   will break some.
2. **Always fall through.** Every accessor reads `CamouwolfConfig` and, when the key
   is absent, runs upstream's code untouched. An unconfigured Camouwolf binary *is*
   Chromium. This is what makes the series safe to carry and a bisect
   meaningful.

## Verifying before you build

`check-dead-patches.py` proves each hunk still **anchors** where it thinks it
does. That is not the same as proving it **compiles**: a patch can apply
perfectly and then fail three hours into a build because a method was renamed.
`check-patch-apis.py` closes that gap, and both run before the build in the
heavy gate and in `ci.yml`.

Every rule in it is a break this project actually hit on the first real build of
Chromium 152:

| What was written | What Chromium 152 wants | How it fails |
| --- | --- | --- |
| `String::FromUTF8` | `String::FromUtf8` | no such member — 16 sites across 8 patches |
| `Split(c, allow_empty, &out)` | `Vector<String> Split(UChar)` | Split now *returns* the vector |
| `base::span(ptr, count)` | `gfx::SkPixmapToWritableSpan()` | the ptr+size ctor is `UNSAFE_BUFFER_USAGE` |
| `SkBitmap` used | `#include ".../SkBitmap.h"` | upstream includes only SkImage/SkSurface |
| static-local cache for a `const String&` getter | assign the member in the ctor | pins the first object's value process-wide |
| `SetIcuTimeZoneAndNotifyV8()` in a ctor | `icu::TimeZone::adoptDefault()` | notify path walks scheduler/pages/workers, none of which exist yet |

The general lesson, worth applying to any new patch: **read the upstream
declaration, do not recall it.** `browser/scripts/fetch-sources.sh` mirrors the
files cheaply, and upstream's own surrounding code is the best style guide —
`media_devices.cc` uses `FromUtf8` two lines from where a patch wanted
`FromUTF8`.

The include check cannot verify headers under a DEPS-managed root (skia, angle,
dawn, v8 …): those live in other repositories, so chromium/src at a tag does not
contain them. Pass `--src /path/to/chromium/src` to check against a real
checkout, which does.

## Authoring

```bash
bash browser/scripts/fetch-sources.sh                  # mirror the pinned tag
cp -r browser/.pristine/$version browser/.work         # work tree
$EDITOR browser/.work/<file>
python3 browser/scripts/mkpatch.py 0018-my-surface <file> [<file>...]
python3 scripts/check-dead-patches.py                  # validate the series
```

`mkpatch.py` diffs the work tree against the mirror and self-checks that the
result reverse-applies. Authoring against the exact tag is why every patch here
applies cleanly rather than "with fuzz" — GNU `patch` will happily land a
fuzzy hunk in the wrong place and report success.

## The series

### 0001 — config plumbing

Adds `base/camouwolf_config.{h,cc}` and `base/camouwolf_farble.h` to `base/BUILD.gn`.

`CamouwolfConfig` reads `CAMOU_CONFIG_1..N` from the environment once per process,
under a `NoDestructor` + `call_once`, and exposes typed getters. Every Chromium
process inherits the environment at spawn, so browser, renderer, GPU and utility
processes reconstruct the same identity with no IPC and no mojo plumbing.

`camouwolf_farble.h` holds the seeded perturbation shared by canvas 2D, canvas
encode, WebGL readback and audio. One helper rather than four, because a
detector that hashes two of those surfaces and compares them catches
independent perturbation — the two hashes stop agreeing the way they do on real
hardware.

### 0002 — navigator identity

`navigator.cc`, `navigator_id.cc`, `navigator_base.cc`, `navigator_concurrent_hardware.cc`,
`navigator_device_memory.cc`.

- **`navigator.webdriver` is hard-`false`.** Upstream returns `true` under either
  the `AutomationControlled` runtime feature (set by `--enable-automation`,
  which every driver passes) or the CDP `Emulation.setAutomationOverride` probe.
  Both are load-bearing tells, and the second means a detector with a CDP
  session can flip the bit itself.
- `platform`, `hardwareConcurrency`, `deviceMemory`, `vendor` are configurable
  and short-circuit the CDP override probes.
- `hardwareConcurrency` is patched in **two** places — `NavigatorBase` (window)
  and `NavigatorConcurrentHardware` (worker) — because a Worker reporting the
  host's real core count while the window reports a spoofed one is a one-line
  check.

### 0003 — user agent + client hints

`components/embedder_support/user_agent_utils.cc`. **The most important patch
for Chrome.**

Upstream's `--user-agent` is an *override*: it moves the UA string and then
either blanks the client-hint metadata or leaves it describing the real host. So
`--user-agent` produces a browser whose UA says Windows and whose
`Sec-CH-UA-Platform` says Linux — checkable server-side, on the first request,
before any JS runs. This patch drives the UA **and** the full hint set from one
config, and `python/src/camouwolf/switches.py` refuses `--user-agent` outright.

Covers the low-entropy hints (`platform`, `mobile`) before the command-line
early-return, and the high-entropy ones (`architecture`, `bitness`, `model`,
`platformVersion`, `uaFullVersion`, `wow64`) after.

**The brand lists** (`userAgentData.brands`, `userAgentData.fullVersionList`,
each a list of `{brand, version}`) are served from `GetUserAgentBrandList()`,
the one function every brand list in the process is built by. A
Chromium-branded build has no product brand, so unconfigured it sends
`"Not?A_Brand";v="24", "Chromium";v="158"` — two entries where every real
Chrome sends three, on every request, with no opt-in. The order is preserved
exactly as given: Chrome permutes the list with a seed derived from the major
version, so the GREASE entry's spelling and position are a function of the
claimed version, and the launcher computes that permutation rather than this
patch recomputing it for the build's own version. `brands` feeds `Sec-CH-UA`
and `navigator.userAgentData.brands`; `fullVersionList` feeds
`Sec-CH-UA-Full-Version-List` and `getHighEntropyValues()`. Either key absent
(or holding no well-formed entry) builds upstream's list for that one.

### 0004 — screen

`screen.cc`, `media_values.cc`. Width, height, avail rect, colour depth, pixel
depth, `isExtended`, and the `device-width` / `device-height` media features,
which a `matchMedia` probe would otherwise answer from the host's screen.

`screen.pixelDepth` falls back to `screen.colorDepth` when absent, which is
Chrome's own invariant; the key exists because Camoufox fingerprints carry both.

`availWidth`/`availHeight` are separate keys rather than derived, because the
launcher enforces `availHeight < height`: CreepJS computes
`noTaskbar = (height === availHeight && width === availWidth)` as a
Like-Headless flag, and that is exactly what stock headless reports.

### 0005 — languages

`navigator_language.cc`. Installed *before* the CDP accept-language probe and
before the `ReduceAcceptLanguage` truncation, so the configured list survives
intact — real Chrome exposes the full list to JS while sending one language in
the header, and the launcher models both halves.

### 0006 — timezone

`base/i18n/icu_util.cc` and `timezone_controller.cc`. Key `timezone`, an IANA
id. Two files because the zone has to be right in two places, and the first
version of this patch covered only one.

- **`InitializeIcuTimeZone()`** (`icu_util.cc`) adopts the configured zone as
  ICU's default. Every process runs it once, straight after ICU's data loads:
  before the sandbox, before V8, before any mojo. So there is no moment at
  which a process holds the host zone, V8 has nothing stale to flush, and
  worker isolates — which read the same process-wide ICU default — agree by
  construction.
- **`TimeZoneController::OnTimeZoneChange()`** ignores the host's zone when one
  is configured.

The second is what the original patch missed, and why a real build reported the
host zone from both `Date` and `Intl`. The renderer subscribes to the browser's
`device::TimeZoneMonitor` during start-up, and `TimeZoneMonitor::AddClient()`
does not wait for a change: it **replies at once** with the zone the browser
captured at its own start-up. Upstream's `OnTimeZoneChange()` adopts whatever
arrives and notifies V8. The constructor hook had adopted the configured zone
correctly; the subscription reply replaced it a few milliseconds later, before
the first script. Nothing was wrong with init order, the sandbox or V8's cache.

With the ICU hook the browser's monitor now captures the configured zone too,
so the reply already carries it; the `OnTimeZoneChange()` guard is still needed
for a real host change, which on Windows includes every `WM_TIMECHANGE`.

Not CDP's `Emulation.setTimezoneOverride`, for two reasons: an override is a
distinct state upstream (`HasTimeZoneOverride()`, and a second caller gets
`kAlreadyInEffect`), and it lands after startup, so early script and workers
can observe the host zone first. The CDP override still works on top of a
configured zone.

Known edge: a host zone change makes `TimeZoneMonitor` re-adopt the host zone
in the **browser** process (renderers ignore it). Nothing page-visible formats
dates there; `services/device/time_zone_monitor/time_zone_monitor.cc`
`UpdateIcuAndNotifyClients()` is where to stop it if that ever matters.

### 0007 — WebGL

`webgl_rendering_context_base.{h,cc}` and `webgl2_rendering_context_base.cc`.
Unmasked vendor/renderer, the `getParameter` limits, the extension list and the
shader precision table.

The strings alone are not the fingerprint. Detectors read `MAX_TEXTURE_SIZE`,
`MAX_VERTEX_UNIFORM_VECTORS` and friends and cross-check them against the
claimed adapter: a renderer string naming an RTX 4070 whose `MAX_TEXTURE_SIZE`
is 8192 (SwiftShader's value) is a mismatch that costs one extra call to find.
`webgl_adapters.json` ships them as whole records so they cannot be mixed.

**`webGl:parameters` / `webGl2:parameters`** — a map of decimal `pname` to
value. A WebGL2 context reads `webGl2:parameters`, a WebGL1 context
`webGl:parameters`; they differ on one GPU (`VERSION`,
`SHADING_LANGUAGE_VERSION`, every WebGL2-only limit) and a page that opens both
compares them. If the identity has no `webGl2:parameters` map at all, WebGL2
falls back to `webGl:parameters` — the pre-existing behaviour. The WebGL2 file
is patched because `WebGL2RenderingContextBase::getParameter` answers the
WebGL2-only names itself and never reaches the base class for them. Values:
number, bool, string, `null` (a configured null), or a list of 2–4 numbers,
which becomes the `Float32Array`/`Int32Array` upstream returns for that name.

**`webGl:supportedExtensions` / `webGl2:supportedExtensions`** — a list of
names, enforced in `ExtensionSupportedAndAllowed()`, the one predicate both
`getSupportedExtensions()` and `getExtension()` ask, so the two cannot
disagree. The list can only *hide*: a name the host cannot provide is not
reported, because an extension object backed by nothing fails on first use.
The result is the configured list ∩ what the build supports, in upstream's
order. No fallback between the two keys — the lists share almost nothing.
`WEBGL_debug_renderer_info` must be in the list for the unmasked strings to be
readable at all.

**`webGl:shaderPrecisionFormats` / `webGl2:shaderPrecisionFormats`** — a map of
`"<shaderType>,<precisionType>"` (decimal, Camoufox's form) to
`{rangeMin, rangeMax, precision}`, served from `getShaderPrecisionFormat()`
after its enum validation.

The launcher sends all three from the fingerprint's own WebGL readout, and only
when that readout names the claimed renderer: the device limits (`MAX_*`,
`MIN_*`, `ALIASED_*`, `SUBPIXEL_BITS`, `UNIFORM_BUFFER_OFFSET_ALIGNMENT`, but no
extension's limits, which the browser would answer whether or not the extension
is enabled), the extension list and the precision table. A Direct3D 11 identity
then reads D3D11's backend signature (`ALIASED_LINE_WIDTH_RANGE` `[1,1]`,
`MAX_VIEWPORT_DIMS` `[32767,32767]`, `[127,127,23]` float precision) and no
OpenGL-only extension. Windows-only extensions the host lacks stay absent: the
list cannot add. A drawn record whose WebGL1 and WebGL2 readouts do not both name
its GPU is redrawn, so every identity has its renderer's limits.

Left out on purpose, following Camoufox: `webGl:contextAttributes` /
`webGl2:contextAttributes` (Camoufox removed them in 156 — the attributes a
page gets back depend on what it asked for, so a fixed answer contradicts the
request) and the `:blockIfNotDefined` switches (`parameters:` removed upstream;
`shaderPrecisionFormats:` returns null for a valid enum, which no real context
does).

### 0008 — WebGPU

`gpu_adapter_info.cc`, `gpu_adapter.cc`, `gpu_supported_limits.{h,cc}`,
`gpu.cc`.

**Adapter info** (`webGpu:vendor`, `webGpu:architecture`, `webGpu:device`) —
the WebGPU twin of `WEBGL_debug_renderer_info`. A detector reading both catches
a browser that patched only WebGL, since the two APIs would name different GPUs
on one machine.

**`webGpu:subgroupMinSize` / `webGpu:subgroupMaxSize`** — the adapter info's
subgroup sizes, which follow the GPU family, so the claimed vendor must not
report the host's.

**`webGpu:features`** — a list of feature names, applied where the adapter's
set is built (`MakeFeatureNameSet`). `requestDevice()` validates
`requiredFeatures` against the same set, so a hidden feature is refused the way
an adapter without it refuses. Hide-only, like the WebGL extension list.

**`webGpu:limits`** — a map of IDL limit name to number, applied to the
adapter's limits in the `GPUAdapter` constructor. Tighten-only: a `max…` limit
becomes `min(host, claimed)`, a `min…Alignment` limit `max(host, claimed)`.
Dawn checks `requiredLimits` against the real adapter, so advertising more
than the host has would be refused on use. Consequence worth knowing: on a
software adapter (`maxTextureDimension2D` 8192) a desktop identity's 16384
cannot be claimed; that needs a real GPU behind the build, not a patch.

**`webGpu:preferredCanvasFormat`** — `"bgra8unorm"` or `"rgba8unorm"`. An OS
tell rather than a GPU one: upstream decides it with `#if`, `rgba8unorm` on
Linux/Android and `bgra8unorm` on Windows/macOS, so a Linux host claiming
Windows gives itself away from `navigator.gpu` alone. Only the script-visible
value changes; the swap chain keeps the host's format.

The launcher claims the canvas format for every Windows and macOS identity, and
the rest only from a recorded adapter whose `unmaskedRenderer` is the claimed
renderer. A Linux identity keeps the build's `rgba8unorm`: fpgen's Linux records
predate the milestone that moved Linux to it. Hide-only features mean a
Windows identity on a host without `shader-f16` does not report it.

Not patched: **`navigator.gpu.wgslLanguageFeatures`**. It is a property of the
Dawn/Tint revision in the build, not of the GPU or the OS — every Chrome 158
reports the same set — so the unconfigured answer is already the right one and
a key could only make it wrong. (Hook, if a bump ever changes that:
`GatherWGSLLanguageFeatures()` in `gpu.cc`, which fills the set in the `GPU`
constructor.) Camoufox has no
`webGpu:*` keys to mirror; these names are Camouwolf's own.

### 0009 — canvas farbling

`base_rendering_context_2d.cc` (getImageData) and `image_data_buffer.cc`
(toDataURL/toBlob).

Both, because the classic canvas fingerprint hashes `toDataURL()` output —
patching only `getImageData` leaves it untouched *and* makes the two disagree,
which is a stronger signal than either hash alone.

The perturbation is **deterministic**: two reads of an unchanged canvas return
the same bytes. Per-read randomness is the loudest possible signal — a page that
gets two different hashes has proved it is being farbled. Real hardware is
stable across reads; so is this. It is skipped on the zero-initialized
readback-failure path, since farbling a buffer that was never filled turns a
transparent canvas into noise.

### 0010 — text metrics

`text_metrics.cc`. Quantizes `measureText().width` to the 26.6 fixed-point grid.

The width's low mantissa bits encode the exact face, version, hinting settings
and rasterizer — far more entropy than the font list. 1/64 px is the grid both
FreeType and DirectWrite use internally, so every value returned is one the
claimed platform could genuinely produce. Rounding to whole pixels (the naive
fix) produces values no real browser reports.

### 0011 — audio

`audio_buffer.{h,cc}` and `realtime_analyser.cc`. Covers both the
OfflineAudioContext fingerprint (render → `getChannelData` → sum) and the
realtime AnalyserNode one.

`getChannelData` perturbs **once** and caches the fact (`camouwolf_farbled_`),
because the returned array is the live buffer: re-perturbing per call would make
a second read differ from the first.

### 0012 — speech voices

`speech_synthesis.cc`. One of the sharpest OS tells there is, because the voice
set is installed by the OS: "Microsoft David" means Windows, "Alex"/"Samantha"
mean macOS, an empty list means a Linux server.

Note the Chrome-specific shape: `voiceURI` equals `name` (Firefox's
`urn:moz-tts:` URIs would be an instant identification), and real Chrome ships
`Google <language>` **network** voices with `localService: false` alongside the
OS ones.

The list fails closed, as Camoufox's `voices:blockIfNotDefined` does: a present
`voices` key replaces the host's list even when it is empty, and the launcher
always sends one (the drawn record's list, or none). Some recorded devices have
no voices, mostly Linux ones, so those identities report an empty list.

### 0013 — media devices

`media_devices.cc`. `enumerateDevices()` needs no permission — labels are hidden
until it is granted, but the count and structure are not. A headless container
reports **zero** devices, which a real consumer desktop essentially never does.

### 0014 — keyboard layout

`keyboard_layout.cc`. `navigator.keyboard.getLayoutMap()` names the physical
keyboard and has nothing to do with `navigator.language` — which is why it is
checked. Resolved locally without touching the mojo service, so the browser
process is never asked for the host layout.

### 0015 — network information

`network_information.cc`. `connection.rtt` and `connection.downlink`, for a
caller who wants a different network than the one measured. A configured value
goes through `RoundRtt`/`RoundMbps` for the page's host, as a measured one does:
a per-host multiplier of 0.9 to 1.1, then 50 ms or 50 kbps steps. Returned raw,
it read the same on every origin and could sit off Chrome's grid. The launcher
writes neither (WINDOWS-DIVERGENCE.md §2.5).

### 0017 — automation invisibility

`runtime_enabled_features.json5`. Makes `AutomationControlled` non-public and
non-settable-from-internals, so there is no path from `--enable-automation` to
script at all.

Belt and braces — 0002 already hard-codes `webdriver` to false — against a
future upstream consumer of the flag landing unnoticed.

**This only covers layer C.** The CDP-side tells (`Runtime.enable`'s
`executionContextCreated` leak, init-script residue, the isolated-world seam)
are the driver's responsibility, handled in `python/src/camouwolf/switches.py` and verified by
the build-tester (`ci/run_build_tester.py`). No browser patch can fix a driver that announces
itself.

### 0018 — ucrt math

`ui/gfx/geometry/sin_cos_degrees.h`, `core/geometry/dom_matrix.cc`,
`platform/transforms/affine_transform.cc`.

Windows resolves the geometry `sin`/`cos`/`atan2` whose results reach content to
ucrtbase; Linux resolves them to glibc, which disagrees by 1 ULP often enough to
read as "not really Windows". `base/camouwolf/camouwolf_ucrt_math.h` carries a bit-exact
replica, gated on the `CAMOUWOLF_UCRT_MATH` environment variable — an environment
variable rather than a config key because gfx needs the answer in every process
and before any config plumbing exists.

Note what is deliberately *not* here: JavaScript's `Math.sin`/`cos`/`tan`. V8
ships its own fdlibm port, so those are already identical on every platform.
Camoufox needs `ucrt-trig-sin-cos-tan.patch` only because SpiderMonkey reaches the
host libm.

### 0019 — per-OS web platform features

`content/child/runtime_features.cc`. A handful of features ship per-OS, and
their absence is as much a platform tell as `navigator.platform`. Web Share is
the loudest: Chrome has had it on Windows/macOS/ChromeOS/Android since 93 and
never on desktop Linux, so a UA claiming Windows with no `navigator.share` is a
one-line detection. Keyed on `navigator.platform` rather than a dedicated key,
so it cannot drift out of step with the identity the rest of the series serves.

Web Bluetooth is the other one: `navigator.bluetooth` and the eight `Bluetooth*`
interfaces are stable on Windows and macOS and experimental on Linux, and they
were the only names a secure page on a real macOS Chrome had that a Windows
identity here lacked, apart from version differences. The browser side is
compiled in on Linux. Without a BlueZ adapter it answers like a machine with no
Bluetooth adapter: `getAvailability()` resolves `false` and `requestDevice()`
rejects with `NotFoundError: Bluetooth adapter not available.` (a real Chrome
with an adapter shows its chooser and waits instead). `EyeDropper` needs
nothing: it is already exposed on every identity, as on Linux Chrome.

### 0020 — system colors

`layout_theme.cc`, the one shared resolver every web-exposed system color passes
through. On a headless Linux host the renderer has no `ui::ColorProvider`, so
resolution falls to `DefaultSystemColor()`, whose hardcoded palette is itself
the tell: `ActiveText` comes back as exactly `rgb(255,0,0)`. Measured on a real
Windows 11 Chrome it is `rgb(0,102,204)`, `Highlight` is `rgb(0,120,215)`,
`GrayText` is `rgb(109,109,109)` — none of which this build produces unspoofed.

The launcher supplies the palette only where the host cannot produce it, so
linux→linux and macos→macos keep the real host palette.

### 0021 — font list

`css_font_selector_base.{h,cc}`, `css_font_selector.cc`,
`offscreen_font_selector.cc`.

The installed-font set is the highest-entropy signal a page can read with no
permission prompt, and the launcher has always drawn a per-identity subset into
`config.fonts` — but until this patch nothing read it. `FONTCONFIG_FILE` bounds
what the *build* can render, per OS; it cannot vary per identity. So every Camouwolf
browser claiming Windows reported a byte-identical font list, which turns the
highest-entropy axis there is into a fleet-wide clone marker.

Gated on the **selector**, not on `FontCache`, and this placement is the whole
design. A page reads the font set two ways — by measuring text in a named family
and comparing the advance against a fallback, and by asking outright through
`document.fonts.check()` or `@font-face src:local()` — and both must give the
same answer, or a page can find a font it cannot render. `CSSFontSelectorBase`
is where both paths still meet *and* where the `FontFamily` still knows whether
it came from a CSS generic keyword or from a name the page chose:

- **Generics fall through.** `serif`, `sans-serif`, `monospace`, `cursive`,
  `fantasy`, `system-ui`, `math` are requests for whatever the platform maps the
  keyword to, not claims about installed faces. Blocking one would leave a
  document with no usable font rather than making it look like another machine.
  By `FontCache` they have already been resolved to a concrete name, which is
  exactly why the gate cannot live there.
- **Web fonts fall through.** The check sits *after* the `@font-face` lookup: a
  downloaded font is something the page shipped, not evidence about this host.
- **`FontFallbackList`'s no-selector branch is untouched.** It serves fonts
  built without a selector — form controls and other non-DOM paths — which are
  not page-readable and would break visibly if filtered.

Camoufox does this by pruning the platform font list behind
`kFontSystemWhitelistPref`. Chromium has no such pref, so the gate goes on the
selector instead.

### 0022 — the platform UI font

`font_cache.cc` (the `system-ui` generic) and
`layout_theme_font_provider_default.cc` (the CSS2 system-font keywords).

One patch, not the two Camoufox carries, because these are the same question asked
through two APIs: *what is this platform's UI font?* A page that gets Segoe UI
from `system-ui` and Arial from `font: menu` has found a machine that does not
exist. CreepJS's headless detector probes all six CSS2 keywords —  `caption`,
`icon`, `menu`, `message-box`, `small-caption`, `status-bar`.

The detail worth reading upstream for rather than assuming: **only three of the
six differ on Windows.** `layout_theme_font_provider_win.cc` routes
`small-caption`, `menu` and `status-bar` through the NONCLIENTMETRICS the
browser reads off the OS — normally Segoe UI — and lets `caption`, `icon` and
`message-box` fall through to `DefaultGUIFont()`, which is `"Arial"` on every
platform. Answering Segoe UI for all six would be exactly as wrong as answering
Arial for all six.

Linux needs no spoof: real Chrome on Linux uses the same `_default.cc` file, so
the unspoofed answer is already right. macOS is untouched — `SystemFontPlatformData`
is compiled out on Mac and `LayoutThemeMac` handles the keywords on a separate
path, and `linux → macos` is refused by the launcher anyway.

Keyed on `navigator.platform`, matching 0019, so it cannot drift out of step
with the identity the rest of the series serves.

There is also an interaction with 0021 worth knowing: these keywords resolve to
a concrete family name, which then passes through the font-list gate. A Windows
identity whose drawn font list omitted Segoe UI would get no system font at
all — which is why the launcher's Windows font manifest always carries it.

### 0024 — touch points

`navigator_events.cc`, `slow_web_preference_cache.cc`. `navigator.maxTouchPoints`
from `navigator.maxTouchPoints`, and the pointer with it, as the identity's
devices rather than the host's: a mouse, and a touchscreen beside it when the
identity claims touch points. A touch identity matches `(any-pointer: coarse)`
and keeps `(pointer: fine)` and `(hover: hover)`, as on a touch laptop; a
touchless one matches only the mouse. `ontouchstart` is left off: desktop
Chrome does not expose the touch event handlers even with a touchscreen (touch
event feature detection defaults to disabled outside Android and iOS).

The pointer is stated whole, headless or not, and per page from the identity of
the page's browser context (0034). Playwright's headless launch states a mouse
on the command line (`--blink-settings`), which the renderer applies after
these preferences and so would answer for every context; the launchers drop it.

### 0025 — device scale factor

`ui/display/display.cc`. `window.devicePixelRatio`, delivered as the **real**
device scale factor — the key makes `Display::HasForceDeviceScaleFactor()` true
and `GetForcedDeviceScaleFactor()` return the value, exactly as
`--force-device-scale-factor` would.

Not an override of the JS getter, because the getter is one reader among
several: `matchMedia('(resolution: 2dppx)')`, `-webkit-device-pixel-ratio`,
`srcset` / `image-set()` selection and layout's pixel snapping all compare
against the same factor, and a getter that says 2 while a media query says 1 is
found in one line. Moving the factor itself moves all of them.

What that costs: the window really is rendered at that scale, so screenshots
and `--window-size` (which is in DIPs) scale with it, and the host screen's
size in DIPs shrinks by the factor — which is why `screen.*` (0004) must be set
alongside. The launchers therefore size the virtual display they own to the
claimed screen times the ratio, and move a ratio no display setting offers (a
scraped page zoom such as 1.0909) to the nearest step, rescaling the CSS
geometry so the screen is still the monitor behind it. Code that reads the `--force-device-scale-factor` switch directly
instead of going through `display::Display` does not see the key. That set
has not been audited (it needs a full checkout: `git grep kForceDeviceScaleFactor`);
on the first real build, compare `devicePixelRatio`, a `resolution` media query
and a screenshot's pixel size against a run with the switch.

### 0026 — ICU default locale

`base/i18n/icubridge/default_icu_locale.cc`. Key **`locale:intl`**, one BCP 47
tag. (Not `locale:all`: the launcher already uses that name for the
comma-joined language list, as Camoufox does. Camoufox builds its Intl default
from `locale:language` + `locale:region`; one explicit tag is used here so the
launcher decides the value, for the reason below.)

`SetDefaultIcuLocale()` is the only function that can move ICU's default locale
— upstream guards it with a pass-key — and ICU's default is what V8 reports
from `Intl.DateTimeFormat().resolvedOptions().locale` and formats
`toLocaleString()` with, in the window and in workers. The configured tag
replaces whatever the caller resolved.

What the callers resolve is the **UI** locale: `--lang=de-DE` is mapped onto
the nearest locale the build ships a `.pak` for and arrives as `de`. The patch
sits below that mapping, so the resource bundle still loads a locale that
exists while ICU gets the tag the identity names.

A separate key rather than reusing `navigator.languages[0]`, deliberately. The
two are not the same value in real Chrome: a German Chrome reports
`navigator.language === "de-DE"` and an Intl default of `"de"`, because Chrome
itself feeds ICU the UI locale; `en-US`, `en-GB`, `pt-BR`, `es-419`, `zh-CN`
keep their region because those are UI locales. So the launcher should write
the tag *real Chrome's UI would resolve* for the identity, not blindly
`languages[0]` — a key of its own lets it. Unset, upstream's mapping of
`--lang` applies, which is already Chrome's.

It outranks CDP's `Emulation.setLocaleOverride` (Playwright's `locale`
option), which funnels through the same function: with the key set that
override is accepted and has no effect, so the two must not be set to
different values.

### 0027 — AudioContext device properties

`audio_context.cc`, `realtime_audio_destination_handler.cc`. The same three
keys as Camoufox's `audio-context-spoofing.patch`.

- **`AudioContext:sampleRate`** — the default rate of `new AudioContext()`.
  Injected in `AudioContext::Create` as though the page had passed
  `{sampleRate}`, not by overriding the getter: the rate decides how the graph
  renders, and the audio fingerprint hashes rendered output. Upstream's
  explicit-rate path renders at the claimed rate and resamples to the device.
  A page's own `sampleRate` option still wins. `OfflineAudioContext` always
  names its rate and is untouched.
- **`AudioContext:outputLatency`** — seconds, served from
  `AudioContext::outputLatency()` through upstream's quantization.
- **`AudioContext:maxChannelCount`** — `destination.maxChannelCount` for
  realtime contexts only (an offline destination has no device), and with it
  the bound on `destination.channelCount`.

Not covered: `baseLatency` (derived from the real callback buffer, and it does
move when the rate is resampled) and the values a page gets from
`enumerateDevices()`/`getUserMedia` capabilities.

### 0028 — IPv4-only network

`ip_address.cc`, `host_resolver_manager.cc`, `tcp_client_socket.cc`,
`udp_socket_posix.cc`, `ipc_network_manager.cc`. With `network:ipv4Only` the
browser behaves as on a network with no IPv6 route: names resolve without AAAA
(as when Chrome's own IPv6 probe fails), a TCP or QUIC connect to a global IPv6
address fails with the error `ENETUNREACH` maps to, and WebRTC gathers no IPv6
address. Loopback, link-local and IPv4-mapped addresses still work, as they do
on a real IPv4-only host. Stopping only the AAAA lookups would be a state no
network produces: IPv6 literals would still connect and a page could read the
host's IPv6 address.

The launcher sets the key on a `geoip` launch without a proxy when this host's
IPv6 address does not geolocate where the IPv4 identity does.

## Not yet patched

Surfaces that were looked at against 158.0.8127.36 and deliberately left alone,
with where the hook would go. Nothing here is a guess at an API — these are the
functions read at the tag.

### WebGL context attributes, `:blockIfNotDefined`

See 0007. Camoufox removed both; nothing to port.

### `navigator.gpu.wgslLanguageFeatures`

See 0008. Follows the build, not the identity.

### macOS key bindings

On a real Mac most editing keys never reach Blink's table: Cocoa's
`interpretKeyEvents:` turns them into edit commands in the browser (Cmd+A is
`selectAll:`, Ctrl+A is `moveToBeginningOfParagraph:`, Home scrolls instead of
moving the caret), and Playwright only sends those commands when it runs on
macOS. Porting Blink's compiled macOS table alone would make a `MacIntel`
identity's keys neither Linux's nor macOS's, so it waits for the Cocoa table to
come with it. The launcher refuses macOS identities on a Linux host unless
`allow_os_mismatch` is set.

### `BarcodeDetector` on macOS

Real macOS Chrome exposes `BarcodeDetector` (it is backed by the Vision
framework); Windows and Linux Chrome do not. Exposing it on a `MacIntel`
identity needs a detector service behind it, or `getSupportedFormats()` answers
an empty list that no Mac gives.

### Window frame geometry

The launcher sets the outer size and position from the identity, and the
browser lays out its own toolbar in it, so `outerWidth − innerWidth` is 0 and
`outerHeight − innerHeight` is 87, headless, on a virtual display and headed
alike. In the recorded Windows and macOS devices the most common frame is a
maximized window with exactly that: width delta 0 and height delta 87 (or 121
with the bookmarks bar). Nothing to patch.

### `history.length`

2 after `new_page()` and `goto()`, as in stock Chrome: the tab's `about:blank`
is the first entry. Real Chrome's value depends on how the tab was opened, so
there is no single constant to serve.

### Input events from the driver

Camoufox patches several input behaviours of Firefox. Measured here with
Playwright driving this build under Xvfb, against the same input from the X
server (XTest) and against what Chromium's native event builders produce on
each OS:

- **Coalescing.** The renderer coalesces protocol mouse moves exactly as it
  coalesces native ones: moves sent faster than frames arrive as one
  `pointermove` whose `getCoalescedEvents()` holds them all (up to 52 per frame
  in a 1000 Hz burst). One event per move is the driver's pacing, since
  Playwright awaits each move's acknowledgement, which comes a frame later.
  Acknowledging early would let `mouse.move` return before the page saw it.
- **Event timestamps.** Chromium on Windows stamps an event with
  `EventTimeForNow()`, not the 15.6 ms message clock, so there is no tick to
  match. On X11 an event's `timeStamp` carries the X server's millisecond time,
  so every event shares one sub-millisecond fraction; protocol events do not.
  That matters only to a Linux identity and is not changed.
- **Wheel units.** A Windows or Linux mouse wheel scrolls by pixels and
  animates; a protocol wheel event is precise and jumps, like a touchpad. Only
  the scroll's timing shows it; 0038 matches the values a page reads.
- **Drag source events.** Blink fires `drag` before each `dragover` for a
  protocol drag as for a native one; Playwright sends a single move.
- **Pointer lock.** Headed, a locked pointer reports `movementX`/`movementY`
  as XTest input does, with the window on screen or partly off it (Camoufox's
  patch is for a GTK pointer barrier that fails off screen; Chromium on X11
  grabs the pointer instead). A plain `headless=True` launch answers a lock with a
  stream of zero-movement moves; use `headless="virtual"`.
- **The cursor (`showcursor`).** Firefox draws a debug cursor in its chrome; a
  page cannot read it, and Chromium has no counterpart.

### 0029 — platform editing behaviour

`web_view_impl.cc`. Blink picks its editing conventions at compile time, and a
page can measure them. Keyed on `navigator.platform`, matching 0019, a `Win32`
identity gets Windows' editing behaviour: Ctrl+Right stops after the space, a
double-click selects the trailing space, and a middle click autoscrolls rather
than pasting the X11 primary selection. A `MacIntel` identity gets macOS's
editing behaviour and smart insert and delete. A Linux identity, and a launch
with no config, keep the build's own. The key bindings follow it in 0033.

### 0030 — WebRTC IP handling switch

`chrome/browser/renderer_preferences_util.cc`. The launcher keeps WebRTC off UDP
with `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` when asked
(`block_webrtc=True`; before 0031 it did so for every proxied launch), but at
158 only `content_shell` and the old headless shell read that switch. The full
browser takes the policy from the `webrtc.ip_handling_policy` pref alone, so the
switch did nothing: with a SOCKS proxy set, stock Chrome 147 and this build both
sent STUN straight from the host and showed its public address as the srflx
candidate. With the switch honoured, a proxied launch gathers what a real Chrome
with that policy gathers: no candidates, `c=IN IP4 0.0.0.0`, and two TCP host
entries (`0.0.0.0` and `::`, port 9) in `getStats()`.

The switch overrides the pref and clears the per-URL `WebRtcIPHandlingUrl` list,
as in `content_shell`; a page cannot see which of the two set the policy.

### 0031 — WebRTC behind a proxy (`webrtc:ipv4`, `webrtc:ipv6`)

`services/network/p2p/`, plus `camouwolf_proxied_p2p.{h,cc}` from
`browser/additions/camouwolfp2p/`. Without it a proxied browser sends STUN and
media straight from the host, so the srflx candidate is the host's own public
address. With it, WebRTC in a proxied context looks like Chrome on a machine at
the proxy's exit.

The browser process's `P2PSocketManager` is per network context, so the route is
decided per context: on first use it resolves the context's proxy for a probe
URL.

- **DIRECT**: nothing changes. An unproxied launch gathers what it gathered
  before.
- **SOCKS5**: each UDP socket is a `ProxiedUdpSocket`, passed in through
  `P2PSocketUdp`'s existing socket-factory constructor. It opens a SOCKS5
  `UDP ASSOCIATE` (RFC 1928) and sends every datagram framed to the relay, so
  STUN, TURN and media leave from the exit and the srflx candidate is the exit's
  address. STUN and TURN names are not resolved on the host: `GetHostAddress`
  answers a placeholder in `240.0.0.0/4`, and the socket sends to that
  placeholder as a domain name (`ATYP 3`), so the proxy resolves it. A relay
  that replies with an unspecified, named or private `BND` address (a public
  proxy behind NAT) is reached at the proxy's own address. The association lives
  as long as its control connection; when the proxy closes it, the socket fails.
- **Any other proxy, or no answer**: nothing may leave the host. An HTTP proxy
  cannot carry UDP, so UDP sockets open with the identity's address and send
  nothing: the page reads a machine whose UDP is blocked. Only a TURN-over-TCP
  server is reachable, through the proxy like any other connection.

The interface list the renderer sees is the identity's network, never the
host's: one adapter, `eth0`, carrying `webrtc:ipv4` (or `webrtc:ipv6`). Local
sockets bind the wildcard and report that address with their real port, and TCP
sockets report it too, so host candidates, the SDP and `getStats()` hold the
identity address. Without media permission the host candidate is still an
mDNS name, as in stock Chrome; a local peer resolving this browser's own
`.local` name gets the identity address (`MdnsResponder::AddressForOwnName`).
Datagrams between two peers on this host still flow: a send to the identity
address goes to loopback, and loopback sources come back as the identity
address.

The launcher writes the exit IP from `geoip` to `webrtc:ipv4` or `webrtc:ipv6`
for a proxied launch. A proxied context without either key has no network and
gathers no host candidate. `block_webrtc=True` still passes the 0030 switch,
which leaves a page no UDP at all.

### 0034 — per-context identity

A Playwright context made by the launchers' `NewContext()` is another machine
than the rest of the browser. Every surface a page can read answers with the
context's identity: Blink in its renderers and workers, the User-Agent,
`Sec-CH-UA-*` and `Accept-Language` headers, the timezone and Intl locale,
WebRTC's addresses, WebGL and WebGPU, and the font list. A plain
`browser.new_context()`, and the default context, keep the launch's identity.

**Why per context and not per page.** Playwright's emulation (`userAgent`,
`locale`, `timezoneId`) is a CDP override in one page's renderer. The page's
workers, the network stack and the GPU process never see it, so a page that
compares itself with a worker, or with its own request headers, reads two
machines. The identity has to be held where all of them look: by the browser
for the context.

**How.**

- `Target.setBrowserContextIdentity(browserContextId, identity, languages)`,
  a CDP command in chrome's `TargetHandler`, stores the identity (the same JSON
  as `CAMOU_CONFIG`) against the browser context with
  `CamouwolfConfig::SetForContext`. The launcher sends it right after creating
  the context, before the context has a renderer. Without `locale:intl` the
  handler writes the UI locale Chrome resolves the first language to, as it
  does the launch's (0026). It calls `l10n_util::CheckAndResolveLocale`
  itself: `GetApplicationLocale` ignores its argument on Linux and answers the
  process's `LANGUAGE`, which is the launch's.
- The context's `Accept-Language` and `navigator.languages` come from the
  context's language prefs, which the command sets from `languages`. Each
  tab's `LanguagePrefs` rebuilds the accept list from the selected list, where
  `--accept-lang` holds the launch's at command-line precedence, so the patch
  reads a selected list set in the context's own prefs ahead of it.
- Each renderer serves one browser context. `RenderProcessHostImpl` hands a
  renderer of an identified context its JSON as a file (`context_identity`),
  and the renderer adopts it in place of the launch's before Blink starts
  (`AdoptForProcess`). Every renderer-side patch, workers included, then reads
  the context's values unchanged.
- In the browser process, code that runs for a context reads
  `CamouwolfConfig::ForContext(context)`, or runs under a `ScopedIdentity`:
  client hints, the user agent of navigations, prefetches, shared and service
  workers, touch points (0024), the WebRTC addresses the socket dispatcher
  hands the network service (0031), and the voices the speech engine lists
  (0032).
- The network service is shared by every context, so it is told rather than
  asked: `NetworkContext.SetUserAgent` gives a context's network context the
  user agent requests without one take, such as a worker's script fetch.

**What a context shares with the launch.** The OS (the fonts and maths
library the process was launched with), the screen and window (one native
window and one display), `navigator.hardwareConcurrency` (one process's
cores), and the browser's behaviour keys (`network:ipv4Only`,
`allowAddonNewtab`, `instantAnimations`, `showcursor`), which the launchers
copy into the context's identity because its renderers read that in place of
the launch's. SOCKS5 credentials are not copied. The launchers draw the rest afresh: GPU, fonts, voices, client hints,
canvas and audio seeds. An unproxied context leaves from the browser's address,
so it keeps the launch's timezone, position, WebRTC address and languages; a
proxied one is placed at the proxy's exit, looked up through the proxy.

**Not supported.** `storageState` with origins' localStorage: Playwright writes
it from a page before the launcher can identify the context. Cookies work.

### 0035 — the identity's platform codecs

`supported_types.cc`, `mojo_audio_encoder.cc`, `media_capabilities.cc`,
WebCodecs `video_decoder.cc`, `video_decoder_broker.cc` and `video_encoder.cc`,
and `video_codec_factory.cc` (WebRTC). Which codecs a browser decodes and
encodes in hardware, and whether it plays HEVC or records AAC at all, is a
property of the OS and the GPU. A Linux build answers for the host on every one
of these surfaces, so a Windows or macOS identity would describe a machine with
no HEVC, no AAC encoder and no hardware codecs.

The launcher takes the answers from the same fpgen record as the GPU, so the
codecs belong to the card that `webgl` and `webgpu` name:

- **`media:platformDecoders`** — codecs the record decodes with
  `prefer-hardware` (`h264`, `vp8`, `vp9`, `av1`, `hevc`, by
  `media::GetCodecName`). It answers WebCodecs `VideoDecoder.isConfigSupported`
  with `prefer-hardware` and `decodingInfo().powerEfficient`; `hevc` also
  answers `canPlayType`, `MediaSource.isTypeSupported` and decode support for
  HEVC Main and Main 10, as Chrome does with a platform HEVC decoder.
- **`media:platformEncoders`** — `h264` and `hevc` when the record's WebRTC
  sender offers H264 High or H265 (formats only a hardware encoder offers),
  and `aac` on a Windows or macOS identity, whose OS ships an AAC encoder. It
  answers `VideoEncoder.isConfigSupported` with `prefer-hardware`,
  `MediaRecorder.isTypeSupported` for HEVC and AAC, and `AudioEncoder`.
- **`media:rtcEncodeFormats`**, **`media:rtcDecodeFormats`** — the hardware
  formats as `"H264|<fmtp>"` / `"H265|<fmtp>"`. Send formats are the record's
  own; receive formats are the H264 High and H265 entries a hardware decoder
  adds, taken from Chrome on macOS (H265 at `level-id=180`). They are appended
  after the software formats, the order `MergeFormats` gives, and
  `RTCRtpSender/Receiver.getCapabilities` and the encoding/decoding info for
  `webrtc` follow them.

A record with no media data claims nothing, and every surface keeps the host's
answer. Not covered: HEVC `prefer-software` encode on macOS, the Windows-only
AAC bitrate check, VP9 and AV1 hardware encode (not recorded), and EME.

A claimed codec the host cannot run fails as a broken platform codec fails in
Chrome: `prefer-hardware` WebCodecs decode falls back to software, and HEVC
playback, hardware encode, AAC encode and H265 or H264 High in WebRTC fail at
use. None of those formats was negotiable before, so nothing that worked stops
working ([`platform-codec-claims-fail-at-use`](../ci/tribal-rules.yml)).

### 0036 — Battery Status API

`battery_dispatcher.cc`. With `battery:charging` and `battery:level` set,
`navigator.getBattery()` answers from them and never asks the host's battery
service, so a laptop host's discharging battery does not show. A charging
battery at level 1 reports `chargingTime` 0, any other state `Infinity`, as
Chrome does for a battery with no time estimate.

The launcher claims a desktop on mains power (charging, level 1): what Chrome
reports for a machine with no battery on Windows, macOS and Linux, and fpgen
records no battery. A caller can pass other values in `config`; a frozen
discharging level is itself unusual, so only do so with a reason.

### 0032 — speech synthesis engine

`content/browser/speech/tts_linux.cc`. 0012 gives the page the identity's voices,
but on a host without speech-dispatcher every `speak()` fired only `error`
(`synthesis-failed`) within a few milliseconds, where a real machine starts,
reports boundaries and ends. With a `voices` key the Linux TTS platform speaks
those voices itself, silently, with the events of the claimed OS's engine:

- a local voice fires `start`, a `word` boundary per whitespace-separated word
  (macOS and Windows), a `sentence` boundary at each sentence (Windows' SAPI
  sends both), and `end`. A Linux identity's voices fire `start` and `end`
  only, as speech-dispatcher does.
- a network voice (`Google …`) fires `start` and `end`, no boundaries.

The pace is measured on macOS Chrome 153 with a 56-character sentence: a local
voice starts after about 4 ms and ends after about 3.3 s (57 ms a character
after a 110 ms lead); a network voice starts after about 409 ms and ends after
about 5 s. Everything after `start` scales with `1 / rate`, as it did there.
`pause()`, `resume()` and `cancel()` behave as on a real engine. With no
`voices` key nothing changes. The voices are read when they are listed, under
the calling context's identity (0034), so a context speaks its own.

### 0033 — platform key bindings

`editing_behavior.cc`. Blink compiles its key bindings per platform. Linux and
Windows Chrome agree on almost all of them (Ctrl+Backspace, Ctrl+Delete,
Home/End, Ctrl+Home/End, Ctrl+Z/Y, Ctrl+A), and differ in three:

- Ctrl+Shift+Backspace deletes to the start of the line on Linux and does
  nothing on Windows;
- Alt+Backspace undoes and Alt+Shift+Backspace redoes on Windows, and do nothing
  on Linux;
- Linux never inserts a character typed with Ctrl held; Windows does (AltGr is
  Ctrl+Alt there).

Those three follow the editing behaviour 0029 picks, so a `Win32` identity gets
Windows' and every other launch keeps Linux's. macOS's bindings are not ported;
see "Not yet patched".

### 0037 — canvas first read

`mailbox_texture_backing.cc`. An accelerated 2D canvas answers its first
`getImageData` from the GPU and, after a few reads, from a CPU copy.
`getImageData` asks for unpremultiplied pixels, and Ganesh unpremultiplied them
in a shader, which rounds low-alpha pixels differently from the CPU. So read 1
of a canvas with antialiased text differed from read 2, on Linux GL, Vulkan and
SwiftShader backends alike, and on no real Windows machine; 0009's farbling
perturbs both reads with one seed and cannot hide it. The backing now reads the
premultiplied pixels back and unpremultiplies them on the CPU, as the later
reads do. `--disable-accelerated-2d-canvas` (the `virtual-gpu` launch) never
had the problem. Regression test: `tests/native/test_canvas_readback.py`.

### 0038 — protocol wheel ticks

`content/browser/devtools/protocol/input_handler.cc`. A protocol wheel event
(`Input.dispatchMouseEvent`, what `mouse.wheel` sends) is one tick whatever its
delta, so `wheelDelta` was ±120 against any `deltaY`. Native builders count a
tick per notch: Windows scrolls 100 px a notch, so `wheelDelta` is 1.2 times
`deltaY`; Linux (Aura) counts a tick per 120 px, and macOS per 40 px
(`kScrollbarPixelsPerCocoaTick`). Keyed on `navigator.platform`, as 0029 is,
the tick count is the delta in those pixels. A launch with no config keeps
upstream's.

### 0039 — protocol drag buttons

`content/browser/devtools/protocol/input_handler.cc`. Playwright drags with
`Input.dispatchDragEvent`, which carries only key modifiers, so `buttons` was 0
on `drag`, `dragenter`, `dragover` and `drop`. A drag is made with the left
button held, and a page reads `buttons == 1` on each of them; `dragend` comes
after the release and stays 0.

### 0040 — trusted events from the automation world

`third_party/blink/renderer/core/dom/events/event_target.cc`. `select_option`
and `fill` on a date, time or colour input set the value from a DevTools
isolated world and dispatch `input` and `change` there, which made them
untrusted; the same choice by a user is trusted. An event dispatched from a
`kInspectorIsolated` world is now trusted. Page script, the main-world
`dispatch_event` and extension worlds still dispatch untrusted events.

### 0041 — SOCKS5 username and password (`proxy:socks5Credentials`)

`net/socket/socks5_client_socket.{h,cc}`, `socks_connect_job.cc`, and 0031's
`ProxiedUdpSocket`. Chromium speaks SOCKS5 without authentication only, so a
proxy that wants a password refused every connection. The client now offers
username/password (RFC 1929) beside no authentication when the launch gave
credentials for that proxy, and sends them if the proxy picks that method; a
refusal fails the connection. The page path (CONNECT) and WebRTC's relay
(UDP ASSOCIATE) share the greeting and request builders.

The launcher takes the credentials off the proxy Playwright sees and writes
them to `proxy:socks5Credentials`, keyed by the proxy as `host:port` (lowercase,
IPv6 in brackets, port 1080 by default), so they never appear in argv. Each
field is 1 to 255 bytes of UTF-8, as RFC 1929 allows; the launcher refuses a
longer one. A context-level SOCKS5 proxy with a password is still refused by
Playwright.

### 0042 — closed shadow roots for automation

`element.{idl,h,cc}`. `Element.shadowRoot` returns a closed root to DevTools'
isolated world, where Playwright's selector engine runs, so a locator reaches
into a closed root as a user's click can. The page's own worlds still get
`null`; no page script can run in that world.

### 0043 — instant animations (`instantAnimations`)

`animation_effect.cc`. Every finite CSS animation, transition and Web
Animation gets a zero iteration duration, so it finishes at once and automation
never waits on one. Infinite animations keep running. Off by default and the
launcher warns: a page reads the zero duration back through
`getComputedTiming()`.

### 0044 — extension tabs (`allowAddonNewtab`)

`tabs_api.cc`, `windows_api.cc`, `runtime_api.cc`. With the key false (the
launcher's default) `chrome.tabs.create`, `chrome.windows.create` and
`chrome.runtime.openOptionsPage` fail with "Opening a tab is disabled", so an
extension's welcome or options page never appears beside the pages automation
opened. The extension sees an API error, not a missing API.

### 0045 — CA certificates (`certificates`)

`profile_network_context_service.cc`. Each base64 DER certificate in the key is
added as a trust anchor exactly as the `CACertificates` policy adds one, so a
launch can trust the CA of a proxy that terminates TLS without a policy file or
a certificate database in the user's home. The launcher turns
`certificatePaths` (PEM or DER files) into this key.

### 0046 — show cursor (`showcursor`)

`web_dev_tools_agent_impl.cc`. Turns on DevTools' click visualization for every
session an automation driver attaches: a 350 ms ripple at each mouse or pointer
press, painted in the frame overlay a page cannot read. It shows where clicks
land, not where the pointer rests: Chromium draws no pointer in a page, and a
hover-only position would need a page-visible element. Off by default.
