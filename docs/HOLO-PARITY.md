# Holo parity

Camouwolf is the Chromium counterpart to `../Holo`. This file tracks, surface by
surface, where the two diverge — what Holo spoofs that Camouwolf does not, what
Camouwolf spoofs that Holo does not, and which Holo features have no Chromium
analogue at all.

It exists because the two projects are meant to be swappable: a caller picks an
engine, not a feature set. A gap here is a caller writing `Camouwolf({...})` and
getting quietly less than `Holo({...})` would have given them.

Audited against Holo at `152.0.3-beta.29` and Camouwolf at Chromium `152.0.7977.42`.

## How to read the status column

| Status | Meaning |
| --- | --- |
| **PARITY** | Same surface covered, by whatever mechanism suits the engine. |
| **GAP** | Holo covers it, Camouwolf does not. Actionable. |
| **DEAD** | The Camouwolf launcher writes the config key; nothing in the browser reads it. Worse than a gap: the launcher believes it landed. |
| **N/A** | Firefox-specific. No Chromium analogue, or upstream Chromium already does it. |
| **CAMOUWOLF+** | Camouwolf covers it and Holo does not. |

---

## 1. Dead config keys

The sharpest class of finding: `src/` writes these into `CAMOU_CONFIG`, and no
`CamouwolfConfig::Get*()` call anywhere in `browser/patches` or `browser/additions`
reads them back. The identity is computed and discarded.

| Key | Holo's reader | Status |
| --- | --- | --- |
| `fonts` | `font-hijacker.patch`, `font-list-spoofing.patch` | FIXED — patch `0021` |
| `fonts:spacing_seed` | `anti-font-fingerprinting.patch` | **DEAD** |
| `webrtc:ipv4` | `webrtc-ip-spoofing.patch` | **DEAD** |
| `geolocation:latitude` / `:longitude` / `:accuracy` | `geolocation-spoofing.patch` | FIXED — carried through Playwright, see §3 |
| `battery:charging` / `:chargingTime` / `:dischargingTime` | Holo `properties.json` | **DEAD** |
| `navigator.maxTouchPoints` | `navigator-maxtouchpoints.patch` | **DEAD** |
| `navigator.globalPrivacyControl` | Holo `properties.json` | **DEAD** |
| `window.outerWidth` / `outerHeight` / `innerWidth` / `innerHeight` | `browser-init.patch` | FIXED — the real window is sized from them |
| `window.screenX` / `screenY` | `browser-init.patch` | FIXED — the real window is positioned from them |
| `screen.pageXOffset` / `pageYOffset` / `pixelDepth` | Holo `properties.json` | **DEAD** |

`locale:language` / `:region` / `:script` / `:all` are *not* dead — they are
launcher-internal, feeding font and voice selection, `navigator.languages` and
the `Accept-Language` header. Same for `webGl:parameters`, which patch 0007
reads through `GetNested`.

### This class of bug is now checked

`scripts/check-config-keys.py` extracts every key `src/` writes and every key
`browser/` reads back, and fails on a key with no consumer. Runs in `ci.yml` and
in the heavy gate. A key delivered some other way must say so in
`CARRIED_BY_LAUNCHER` (geolocation goes through Playwright, `window.*` through
`--window-size`); the remaining known-dead keys are listed explicitly with a
reason, and that list fails if one of them later gains a reader — so it cannot
quietly become a dumping ground.

Nothing caught this before because the two halves live in different languages
and are joined by a string.

### Why `fonts` being dead is the worst of these

`generateFonts()` draws a per-identity subset so two Camouwolf browsers on the same
target OS do not look like the same machine. Nothing consumes it. Camouwolf's only
font control is `FONTCONFIG_FILE`, which is scoped per **OS**, not per
**identity** — so every Camouwolf identity claiming Windows reports a byte-identical
font list. The installed-font set is the highest-entropy JS signal there is
(~13.9 bits; IMC'20 calls it the most fingerprintable single attribute), and
right now Camouwolf uses it as a fleet-wide clone marker rather than a
differentiator.

## 2. Per-context identity

`src/context.ts:buildInitScript` emits calls to seventeen `window.setXxx`
setters, listed in `PERCONTEXT_SETTER_NAMES` (`src/utils.ts:478`). **None of the
seventeen exists in any Camouwolf patch.** Every call is `typeof`-guarded, so
`NewContext()` silently applies only what Playwright's own context options
cover: `userAgent`, `viewport`, `timezoneId`, `locale`, `geolocation`.

Everything else no-ops: `navigator.platform`, screen geometry, WebGL
vendor/renderer, font list, speech voices, `deviceMemory`,
`hardwareConcurrency`, UA-CH platform and platform version, the canvas seed, the
WebRTC IP.

This is not only a missing feature, it is a correctness bug.
`NewContext({ os: "macos" })` on a Windows launch returns a context whose
`User-Agent` header and `navigator.userAgent` say macOS while
`navigator.platform` still says `Win32` and `Sec-CH-UA-Platform` still says
`"Windows"` — exactly the desync `src/clienthints.ts` exists to prevent. And
`assertCoherent()` does not run on the `NewContext` path, so nothing catches it.

Holo backs this with seven per-user-context managers:
`NavigatorManager`, `ScreenDimensionManager`, `WebGLParamsManager`,
`FontListManager`, `SpeechVoicesManager`, `TimezoneManager`, `WebRTCIPManager`.

Status: **GAP**, highest correctness priority.

### The worker problem, and the interim guard

Nearly every one of those surfaces has a **worker twin**. A Worker reads
`navigator.platform`, `hardwareConcurrency`, `deviceMemory`, `languages` and
`userAgentData` from its own global; an OffscreenCanvas in a worker has its own
WebGL context and its own font selector (`OffscreenFontSelector` — the one
patch `0021` also had to gate). Playwright's per-context options reach the
window and the network stack. They do not reach a worker's global.

So a per-context mechanism that moves the window without moving workers does
not merely fall short — it *manufactures* the tell patch `0002` patches
`hardwareConcurrency` in two separate places to avoid. Any real implementation
needs the overrides propagated into `GlobalScopeCreationParams` at worker
creation, which is a 40-argument constructor with call sites across dedicated
workers, shared workers, service workers and worklets.

Until that lands, `NewContext` **refuses** to move a worker-visible surface
(`incoherentContextKeys`) rather than applying half an identity. `timezone`,
`locale`, `geolocation` and `proxy` are exempt: Chromium does apply those per
context, workers included. `allow_incoherent: true` overrides the refusal.

## 3. Browser surfaces

### Missing in Camouwolf

| Surface | Holo patch | Notes |
| --- | --- | --- |
| Per-identity glyph spacing | `anti-font-fingerprinting` | |
| Windows glyph rasterization | `windows-dwrite-rasterization` + `browser/bundle/dwrite/` | Holo runs the real `DWrite.dll` in-process via a from-scratch PE loader. Camouwolf has no `dwrite` reference anywhere, so a Windows persona on Linux rasterizes FreeType-shaped glyphs. **Tracked, deferred** — see §7. |
| WebRTC candidate IP | `webrtc-ip-spoofing` | Camouwolf has `block_webrtc` only, which is a blunter and more visible instrument. |
| Battery | Holo `properties.json` | |
| `navigator.maxTouchPoints` | `navigator-maxtouchpoints` | |
| Referrer of the first navigation | `referrer-spoofing` | Plus the `referrer` / `referrer_allow_same_site` options and the `headers.Referer` key. |
| Pointer / hover media capabilities | `force-default-pointer` | |
| WebGL `supportedExtensions`, `contextAttributes`, `shaderPrecisionFormats` | `webgl-spoofing` | **BLOCKED on CHROME capture data.** Holo has 33 complete records in `webgl_data.json` — but they are Firefox captures and cannot be reused. See §8. |
| The whole `webGl2:*` family | `webgl-spoofing` | **BLOCKED on Chrome capture data**, as above. |
| `AudioContext` `sampleRate`, `outputLatency`, `maxChannelCount` | `audio-context-spoofing` | **Not a gap.** Holo declares these and its launcher never writes them either — they are dead config in Holo too. See §9. |
| Closed shadow-root piercing | `shadow-root-bypass` | Automation convenience, not stealth. |
| `disableInstantAnimations` | `no-css-animations` | |
| `disableTheming` | `global-style-sheets` | |

### Covered by a different mechanism — verify, do not port

| Surface | Holo | Camouwolf |
| --- | --- | --- |
| Debugger invisible to content | `debugger-invisible-to-content` (JS-engine level) | `src/switches.ts` + `tests/compare/cdp-leak.mjs`. Different layer; needs a probe to confirm equivalence. |
| Window geometry | `browser-init` | Camouwolf moves the REAL window (`--window-size` / `--window-position` driven from the config) rather than spoofing the getters. Chromium derives `window.outer*` and `screenX/Y` from the real rect, so spoofing them independently would make the reported chrome height (`outer - inner`) a number no window manager produces. **PARITY.** |
| Geolocation | `geolocation-spoofing` | Applied through Playwright's context geolocation + permission grant (`applyGeolocationDefaults`), not a patch. Chromium's geolocation override lives in the browser process and is not a state a page can interrogate — unlike the timezone override, where upstream tracks `HasTimeZoneOverride()`. **PARITY.** |
| Trusted automation events | `trusted-automation-events` | CDP `Input.dispatch*Event` already produces trusted events in Chromium. **N/A.** |
| `Math.sin`/`cos`/`tan` host-libm parity | `ucrt-trig-sin-cos-tan` (SpiderMonkey reaches host libm) | V8 ships its own fdlibm port, so `Math.*` is already platform-independent. **N/A.** |

### Firefox-only — no Chromium analogue

`no-search-engines`, `pin-addons`, `all-addons-private-mode`,
`disable-extension-newtab`, `chromeutil`, `holo-firefox-ua-name`,
`cross-process-storage`, `macos-sandbox-crash-fix`,
`sessionstore-popup-close-crash`, `windows-theming-bug-modified`, and the
`librewolf/` and `ghostery/` vendored sets. **N/A.**

### Camouwolf-only

Surfaces Camouwolf covers that Holo has no equivalent for: **CAMOUWOLF+**

- User-Agent Client Hints (`0003`) — Firefox does not implement UA-CH at all
- WebGPU adapter identity (`0008`)
- Keyboard layout map (`0014`)
- Network Information `rtt` / `downlink` (`0015`)
- Storage quota (`0016`)
- Per-OS web platform features, e.g. `navigator.share` (`0019`)

## 4. Launcher option surface

| Holo option | Camouwolf | Status |
| --- | --- | --- |
| `disable_coop` | — | **N/A.** Holo sets `browser.tabs.remote.useCrossOriginOpenerPolicy = false`, which stops COOP from forcing a browsing-context-group swap — the thing that breaks juggler's frame tracking. Chromium performs those swaps natively and Playwright's CDP target model follows them, so there is nothing to disable. |
| `referrer`, `referrer_allow_same_site` | — | **GAP** (needs the browser patch too) |
| `isolated_world_eval` | — | **GAP**, low priority. Playwright-level wrapper, no browser patch: Playwright already models a `"utility"` world internally and simply does not expose it. |
| `main_world_eval` | — | **N/A.** Playwright's `evaluate()` already defaults to `world: "main"` on every engine (`frames.js:597`). Holo needs the option only because its juggler patches move evaluate out of the main world. |
| `addons`, `exclude_addons` | `extensions` | Partial. Camouwolf loads unpacked dirs via `--load-extension` but has no default set (Holo ships uBlock Origin), no download, and no path validation — Holo's `confirmPaths()` throws `InvalidAddonPath` on a bad path; Camouwolf passes it straight to Chromium. |
| `allow_addon_new_tab` | — | **N/A** (Firefox addon plumbing). |
| `firefox_user_prefs` | — | **N/A.** Chromium has no prefs channel; `CAMOU_CONFIG` is the equivalent. |
| `ff_version` | `chrome_version` | **PARITY** (renamed). |

Everything else — `config`, `i_know_what_im_doing`, `os`, `block_images`,
`block_webrtc`, `block_webgl`, `geoip`, `locale`, `fonts`,
`custom_fonts_only`, `screen`, `window`, `fingerprint`, `fingerprint_preset`,
`stealth`, `seed`, `headless`, `gpu`, `require_gpu`, `allow_os_mismatch`,
`executable_path`, `proxy`, `enable_cache`, `args`, `env`, `debug`,
`virtual_display`, `webgl_config` — is **PARITY**.

## 5. Config validation

Holo declares all 109 config properties in `browser/settings/properties.json`
and validates against it in `validateConfig()`, throwing `UnknownProperty` or
`InvalidPropertyType`.

Camouwolf defines `UnknownProperty` in `src/exceptions.ts:29` and never throws it.
There is no `properties.json` — `browser/settings/` is empty — and no
`validateConfig()`. A typo'd key in a hand-written `config: {...}` silently does
nothing. `assertCoherent()` catches contradictions between keys, not keys that
do not exist.

This is also the mechanism that would have caught every entry in §1.

Status: **GAP**. Land it *after* the §1 keys have readers, or it will reject
configs the launcher itself produces.

## 6. CLI, tests, docs

**CLI.** Camouwolf lacks `test`, `server` and `version`; `camouwolf fetch` has no
update check, no `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` handling and no GeoIP
bootstrap. Camouwolf adds `identity`, `presets` and `geoip` (**CAMOUWOLF+**).

**Tests.** Holo has 12 launcher suites, 28 `tests/compare` probes and 4 CI
scripts. Camouwolf has 3, 3 and 1. Missing suites with a direct Camouwolf analogue:
`webrtc-leak`, `world-isolation`, `display-bound`, `virtdisplay`, `voices`,
`exceptions`, `utils`, `basics`. Missing rigs: `capture/`, `harness/`,
`service-tester/`.

**Docs.** ~~`docs/PATCHES.md` says "17 diffs"~~ — fixed; `0018`–`0021` are documented. `browser/docs/` is empty (Holo documents the
per-context patch contract there, which §2 will need).

## 7. Deferred: DWrite

The Windows glyph-rasterization port is **in scope but deferred**. It is the
single largest item here — a vendored `DWrite.dll`, a PE loader, a font map, and
a bundle path — and it only affects Windows-persona-on-Linux launches. Until it
lands, Camouwolf's Windows personas render Linux-shaped glyphs, which
`measureText()` will show to anyone who looks.

Until then, treat Camouwolf as coherent for same-OS personas and
`linux → windows` as best-effort on everything except glyph shape.

## 8. WebGL: why Holo's capture data cannot be reused

Holo's `src/data-files/webgl_data.json` holds 33 complete adapter records —
`contextAttributes`, `supportedExtensions`, the full `parameters` map and
`shaderPrecisionFormats`, for both WebGL1 and WebGL2, each weighted by host OS.
Camouwolf's `webgl_adapters.json` has vendor, renderer, WebGPU info and ten numeric
limits. The obvious move is to copy Holo's across. It does not work, and the
reason is worth writing down so nobody tries it twice.

**The data is Firefox's, and WebGL is an engine-dependent surface.** Measured
against the file:

| Parameter | Holo's records | Real Chrome |
| --- | --- | --- |
| `GL_VENDOR` (7936) | `"Mozilla"` — all 33 records | `"WebKit"` |
| `GL_VERSION` (7938) | `"WebGL 1.0"` | `"WebGL 1.0 (OpenGL ES 2.0 Chromium)"` |
| `SHADING_LANGUAGE_VERSION` (35724) | `"WebGL GLSL ES 1.0"` | `"WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"` |

The extension lists are Firefox-shaped too — `KHR_parallel_shader_compile`,
`WEBGL_blend_func_extended`, `WEBGL_polygon_mode`, `EXT_conservative_depth`,
`EXT_render_snorm` and `NV_shader_noperspective_interpolation` appear in no Holo
record, and Chrome exposes all of them. A Camouwolf persona serving this data would
be a "Chrome" whose `GL_VENDOR` is `Mozilla`. That is not a subtle tell.

Note Camouwolf's 0007 correctly leaves the **masked** `GL_VENDOR`/`GL_RENDERER`
alone: real Chromium already answers `"WebKit"` / `"WebKit WebGL"` there, which
is the right answer for Chrome. Only the unmasked
`WEBGL_debug_renderer_info` pair is substituted.

### What each field actually depends on

| Field | Depends on | Transferable from Holo? |
| --- | --- | --- |
| Fonts (for contrast) | **OS only** — the font set is a property of the operating system, not the browser | **Yes, and already shared.** Identical manifests, and `restore-fonts.sh` reads the same cache. |
| `supportedExtensions` | **Browser engine** first, then GPU + driver + backend | No |
| `GL_VENDOR` / `GL_VERSION` / `SHADING_LANGUAGE_VERSION` | **Browser engine** | No |
| Numeric `parameters` (`MAX_TEXTURE_SIZE`, `MAX_VIEWPORT_DIMS`, …) | **GPU + driver + ANGLE backend.** OS matters only through which backend is used — D3D11 on Windows, Metal on macOS, GL on Linux | Partly, and not worth the risk of mixing |
| `shaderPrecisionFormats` | GPU + driver + backend | Partly |
| `contextAttributes` | Spec defaults; essentially browser. Low entropy | Yes, but barely worth it |

Not CPU, in any case.

So the capture must come from real **Chrome**, on each of Windows, macOS and
Linux, across a handful of GPUs. The existing `webgl_adapters.json` `_comment`
already states the rule this has to follow: a record is a coherent unit and must
be drawn whole.

## 9. Audio: Holo removed this on purpose

Camouwolf carries `0011-audio-farbling`; Holo used to carry
`audio-fingerprint-manager.patch` and **deleted it**. The rationale, from
`src/utils.ts`:

> OfflineAudioContext output is pure CPU math (a CLASS signal:
> browser+OS+CPU-arch, NOT per-device; it never touches audio hardware). Since
> we only spoof WITHIN a CPU arch (linux→win = x86→x86, mac→mac on real Mac
> hardware), stock Firefox audio is already byte-identical to the genuine
> target — verified Linux-x86 Holo == real Win11 FF152 x86
> (comp=4041dfff15e80000, osc=40db6aa27903ab00). Per-identity audio noise was
> actively harmful (drifting value matching no real reference — the Scrapfly
> audio-fingerprint-math tell). Fleet variation comes from presenting different
> CLASSES, never from perturbing within one.

Two consequences for Camouwolf:

1. **The scalars are not a gap.** `AudioContext:outputLatency` is declared in
   Holo's `properties.json` and read by its patch, but Holo's launcher never
   writes it. It is dead config in Holo exactly as it was in Camouwolf.
2. **`0011` is a liability, not a feature.** It is gated on `canvas:seed`, which
   the launcher defaults to `0`, so it is off and Camouwolf's audio is stock
   Chromium today — which is the correct state. But the patch is one config
   value away from reintroducing precisely the tell Holo removed.

**The reasoning transfers; the verification does not.** Holo measured
Linux-x86 Firefox against real Win11 Firefox. Nobody has measured Linux-x86
Chromium against real Win11 Chrome. That is the check to run before deciding
whether `0011` is deleted or merely documented as never-enable — and the Win11
box is the instrument for it.

## 10. A stale claim in `camouwolf_farble.h`

The header says the perturbation is "shared by every readback surface (canvas 2D
getImageData, canvas toDataURL/toBlob, **WebGL readPixels**)". Patch `0009`
touches `base_rendering_context_2d.cc` and `image_data_buffer.cc` only —
`WebGLRenderingContextBase::readPixels` is not farbled. The comment's own
argument is that a detector comparing two readback surfaces catches independent
perturbation, so the gap it warns about is the one it has. Harmless today
because farbling is off by default; it should be fixed or the claim narrowed
before anyone enables it.

---

## 11. Measured against real Windows Chrome (2026-08-19)

`docs/WINDOWS-DIVERGENCE.md` records a direct A/B of every readable surface
against real Google Chrome 151 on Windows 11, captured windowed on the
ground-truth box. Three sections above are superseded by measurement:

- **§8 (WebGL blocked on capture data) is unblocked.** The Windows leg is a
  complete real-Chrome **D3D11** record — `contextAttributes`,
  `supportedExtensions`, every numeric parameter and all twelve
  `shaderPrecisionFormats`, for WebGL1 and WebGL2. It is Chrome-shaped, so
  unlike Holo's `webgl_data.json` it can be used directly.
- **§9 (audio removed on purpose) is confirmed and sharpened.** The classic
  fingerprint window is bit-identical between the two platforms; splitting the
  render into 45 chunks, exactly one (samples 16000-16999) differs. The residue
  is ULP-scale platform math, not a per-device signal, so `0011` stays off.
- **§10 (`camouwolf_farble.h` overclaims WebGL readPixels) still stands** and is
  unchanged by the measurement.

The measurement also found a patch that manufactures its own tell
(`0022` spoofs the system-UI font family but not its size) and a stale
`Sec-CH-UA-Platform-Version` ladder. See that document for the full list.
