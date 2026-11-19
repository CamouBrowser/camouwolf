# The patch series

27 diffs in `browser/patches/`, applied in `series` order against the Chromium
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

`screen.cc`. Width, height, avail rect, colour depth, pixel depth,
`isExtended`.

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

`network_information.cc`. `rtt` and `downlink`. Low entropy on their own, but a
strong residency signal beside the claimed timezone and exit IP: a "residential
US" identity reporting 0 ms RTT is reporting a datacenter link.

### 0016 — storage quota

`storage_manager.cc`. Chrome sets the quota to ~60% of free disk, making
`navigator.storage.estimate().quota` a direct read of host free space — stable,
high-entropy, and uncorrelated with anything the identity claims. Two
"different" browsers reporting the same odd quota are the same machine.

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

`navigator_events.cc`. `navigator.maxTouchPoints` from `navigator.maxTouchPoints`.

The count only. `ontouchstart` and the `pointer` / `any-pointer` media features
come from separate settings the host decides, so a non-zero value is coherent
only for an identity whose other touch signals the launcher has arranged too
(`--touch-events=enabled`, or a mobile emulation). Desktop identities leave the
key out, or write 0.

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

## Not yet patched

Surfaces that were looked at against 158.0.8127.36 and deliberately left alone,
with where the hook would go. Nothing here is a guess at an API — these are the
functions read at the tag.

### WebRTC local address (`webrtc:ipv4`, `webrtc:ipv6`)

No patch, because there is no single place in Chromium where the address a page
sees can be changed without also changing the address the socket binds.

What a page can read, and where each comes from:

- **Host candidates.** The renderer's interface list arrives in
  `IpcNetworkManager::OnNetworkListChanged()`
  (`third_party/blink/renderer/platform/p2p/ipc_network_manager.cc`), which
  turns each `net::NetworkInterface` into a `webrtc::Network` (`AddIP`,
  `set_default_local_addresses`). This looks like the hook and is not: the
  port allocator binds its UDP/TCP sockets to those same addresses through
  `IpcPacketSocketFactory` (`ipc_socket_factory.cc`), so a substituted address
  that is not on the host fails to bind and the connection gathers nothing.
- **mDNS obfuscation.** Without camera/microphone permission a stock Chrome
  never shows the address at all: `FilteringNetworkManager`
  (`filtering_network_manager.cc`, `GetNetworks()` / `GetMdnsResponder()`)
  exposes only the default route, and the candidate's address is replaced by a
  `<uuid>.local` name from `MdnsResponderAdapter` (`mdns_responder_adapter.cc`).
  For that case — every page that has not been granted media — the unpatched
  build already shows what Chrome shows, and there is nothing to spoof.
- **Server-reflexive candidates.** The address is whatever the STUN server saw.
  It can be made right only by routing (the proxy), never by a renderer patch.

So the only readers of a real local address are pages holding media
permission, and for them the value appears in three places that would each need
the same rewrite, at the boundary to script rather than in the ICE stack:

1. `RTCPeerConnectionHandler::OnIceCandidate()` → `OnIceCandidateImpl()` →
   `DidGenerateICECandidate()` (`modules/peerconnection/rtc_peer_connection_handler.cc`)
   — the `icecandidate` event;
2. `RTCPeerConnectionHandler::OnSessionDescriptionsUpdated()` (same file) — the
   `a=candidate` and `c=` lines of `localDescription`;
3. the `local-candidate` entries of `getStats()` (`RTCPeerConnectionHandler::GetStats()`
   and the report conversion under `modules/peerconnection/`).

That is what Camoufox's `webrtc-ip-spoofing.patch` does on Gecko (rewrite SDP,
candidates and stats; fabricate a host candidate when none was gathered). A
Chromium port is three hooks plus the remote side's view: the peer still
receives the rewritten candidate and will try to connect to it, so the
rewritten address has to be unroutable-but-plausible or connectivity checks
change shape. It should be written against a build that can run a loopback
`RTCPeerConnection` test, not blind.

Until then the control is `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`,
which the launcher passes with `block_webrtc`, and by default whenever there is
a proxy; `webrtc:ipv4` stays in `KNOWN_DEAD` in `check-config-keys.py`.

### WebGL context attributes, `:blockIfNotDefined`

See 0007. Camoufox removed both; nothing to port.

### `navigator.gpu.wgslLanguageFeatures`

See 0008. Follows the build, not the identity.
