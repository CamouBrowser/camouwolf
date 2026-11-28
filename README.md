<p align="center">
  <img src="browser/assets/CamouwolfLogo.svg" alt="Camouwolf" width="160">
</p>

<h1 align="center">Camouwolf</h1>

<h4 align="center">An open source anti-detect Chromium for web scraping and AI agents.</h4>

Camouwolf is the Chromium counterpart of
[Camoufox](https://github.com/daijro/camoufox): the same idea and the same
launcher API, on Chrome's engine.

A launch draws a whole device identity -- user agent and client hints, screen,
GPU, fonts, locale, timezone -- from a model of real-world traffic, checks that
its parts agree, and hands it to the browser. The browser answers with it from
C++, so a page sees one consistent machine and no injected script.

**Under active development.** Camouwolf is working to reach the standard
Camoufox holds; expect gaps and breaking changes until it does. Only Linux
x86_64 is built; `python3 -m ci.targets` lists the other targets and why each is
off.

**It claims Chromium, not Chrome.** The browser sends Chromium's own brand list
(`Chromium` and one GREASE entry) and offers only local speech voices. Emulating
Google Chrome itself (the `"Google Chrome"` brand, Google's network voices) is
part of Camouwolf Pro.

## Quick start

```bash
pip install -U "camouwolf[geoip]"    # or: npm install camouwolf
camouwolf fetch                      # the browser, the fpgen model, GeoIP, uBlock Origin Lite
```

```python
from camouwolf.sync_api import Camouwolf

with Camouwolf(os="windows", headless="virtual", humanize=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

Every option is in [`python/README.md`](python/README.md); the TypeScript
launcher takes the same options in camelCase.

## Features

Each row names the patch in [`browser/patches/`](browser/patches) that
implements it; [`docs/PATCHES.md`](docs/PATCHES.md) describes each one. A value
is spoofed when it identifies the host machine or the network. Anything else
stays as Chromium has it, because something stock Chromium never does is a
fingerprint in its own right.

### Fingerprint spoofing

| Trait | What a page sees | Where |
|---|---|---|
| Navigator: platform, hardware concurrency, device memory, vendor | The drawn device's values, in workers too | `0002` |
| User agent and client hints, in script and in headers | One consistent claim of the drawn OS and Chromium version | `0003` |
| Screen, window and device pixel ratio | The drawn device's display, in JS, CSS media queries and layout | `0004`, `0025` |
| Languages, `Intl` and the default locale | The identity's, matching `Accept-Language` | `0005`, `0026` |
| Timezone | The identity's, or the proxy exit's with `geoip=True` | `0006` |
| WebGL and WebGPU | One GPU fpgen recorded for Chrome on that OS, with its parameters, extensions and adapter | `0007`, `0008` |
| Canvas readback | The host's pixels when the identity is on the host's OS and GPU, which the launcher then claims; Brave's per-site farbling when it is not (`canvas_noise` overrides) | `0037`, `0047` |
| Audio device | The output device's sample rate, latency and channels | `0027` |
| Fonts | The claimed OS's fonts, a per-identity subset, and its `system-ui` font | `0021`, `0022` |
| Speech voices | The claimed OS's voices, which speak at a real engine's pace | `0012`, `0032` |
| Microphones, cameras, speakers | OS-style labels and groups | `0013` |
| Keyboard layout, network information, battery | The identity's | `0014`, `0015`, `0036` |
| Media codecs | The hardware decoders and encoders of the identity's GPU and OS | `0035` |
| Touch points | The claimed device's digitizer | `0024` |
| System colors, web platform features, editing and key bindings | The claimed OS's | `0019`, `0020`, `0029`, `0033` |
| Windows math | `sin`, `cos` and `atan2` as Windows computes them | `0018` |
| CPU arithmetic | Skia's drawing and Opus's encoding as the identity's CPU computes them: another x86 vendor's reciprocal estimates, or ARM's NEON code on an x86 host | `0048` |

Left as stock Chromium has it, on purpose:

| Trait | What a page sees | Why it is not spoofed |
|---|---|---|
| WebGL `readPixels` | What the GPU produces, the same on every read | Brave leaves it alone too, and stock Chrome does not perturb pixels ([`no-readback-noise`](ci/tribal-rules.yml)) |
| Text widths | What the font really measures | A width no real installation produces is a fingerprint |
| Audio render (`OfflineAudioContext`, `AnalyserNode`) | What the host CPU computes, the same in every context and launch | A noised value is one no real machine produces |

### Network

| Feature | Where |
|---|---|
| Timezone, locale and geolocation from the proxy's exit IP | launcher, `geoip=True` |
| WebRTC looks like a machine at the proxy's exit: UDP through a SOCKS5 relay, the exit's address | `0030`, `0031` |
| SOCKS5 proxies with a username and password | `0041` |
| IPv4-only without a proxy, so an IPv6 address cannot place the host elsewhere | `0028` |
| CA certificates for a proxy that terminates TLS | `0045` |

### Hiding automation

| What | Where |
|---|---|
| No automation flags, banners or properties a page can read | `0017` |
| `select_option` and date fills fire trusted events | `0040` |
| Wheel events carry the claimed OS's ticks; drags hold the left button | `0038`, `0039` |
| Many identities in one browser: each `NewContext()` is another machine | `0034` |

### Automation

| Feature | How to use it | Where |
|---|---|---|
| Human cursor movement | `humanize=True` | launcher |
| Locators reach into closed shadow roots; a page still gets `null` | default | `0042` |
| Finish animations at once (a page can read it) | `instant_animations=True` | `0043` |
| Show where automation clicks | `show_cursor=True` | `0046` |
| Extensions cannot open tabs unless allowed | `allow_addon_new_tab=True` | `0044` |
| Headed browser on a private display (Linux) | `headless="virtual"` or `"virtual-gpu"` | launcher |
| Install, pin and switch browser builds | `camouwolf fetch`, `set`, `list`, `remove` | launcher |

## Layout

| Path | What it is |
|---|---|
| [`browser/`](browser/) | The patch series, the C++ config layer and the build scripts, pinned to a Chromium tag in `browser/upstream.sh`. Each patch is described in [`docs/PATCHES.md`](docs/PATCHES.md). |
| [`python/`](python/README.md) | The `camouwolf` Python package, the reference launcher. |
| [`typescript/`](typescript/) | The `camouwolf` npm package, a port of the Python launcher. |
| [`tests/`](tests/README.md) | Suites that need a built browser. |
| [`ci/`](ci/README.md) | The test pipeline, the same locally and in GitHub Actions. |

## Building the browser

Linux only. A cold build needs about 100 GB of disk and several hours; a warm
rebuild after a patch edit takes 10 to 20 minutes.

```bash
bash browser/scripts/build.sh                                   # sync Chromium at the pin, apply the series, compile
bash scripts/build-camouwolf-incremental.sh                     # warm tree: re-apply and rebuild what changed
bash scripts/release-camouwolf.sh --target linux --no-release   # package into browser/dist/
```

The header of `browser/scripts/build.sh` lists the environment variables that
cap CPU, disk and download rate. Changing a patch is described in
[`AGENTS.md`](AGENTS.md#changing-a-patch).

## Testing

```bash
pip install -r ci/requirements.txt ./python
python3 -m ci.run_patch_guards                         # the series against the pinned tag
python3 -m ci.run_pythonlib
python3 -m ci.run_typescript                           # Node 22.15 or newer
python3 -m ci.run_build_tester --binary <camouwolf>    # what a page reads vs what was claimed
python3 -m ci.run_playwright --binary <camouwolf>      # the upstream Playwright suite
```

Every stage, and how the workflows use them, is in [`ci/README.md`](ci/README.md).
Contribution rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`AGENTS.md`](AGENTS.md).

## License

See [`LICENSE.md`](LICENSE.md).
