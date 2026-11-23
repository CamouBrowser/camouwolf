<div align="center">

# camouwolf (Python)

#### The Python launcher for the Camouwolf anti-detect browser.

</div>

`camouwolf` drives the Camouwolf browser, a patched Chromium, through
Playwright. It is the Chromium counterpart of
[Camoufox](https://github.com/daijro/camoufox)'s Python launcher and keeps its
API: on every launch it draws a Chrome device identity from
[fpgen](https://github.com/scrapfly/fingerprint-generator), a model of
real-world traffic, checks that the parts agree with each other, and hands it
to the browser, which answers with it from C++.

## Install

```bash
pip install -U "camouwolf[geoip]"
camouwolf fetch
```

`camouwolf fetch` installs the browser from the
[CamouBrowser/camouwolf](https://github.com/CamouBrowser/camouwolf) releases, the
fpgen model, the GeoIP database, and the default addon (uBlock Origin Lite,
from the release pinned in `src/camouwolf/data/ubo.json`).

The parts that do not depend on the browser engine -- locales, GeoIP, proxy
lookup, the fpgen model pin, the virtual display -- come from
[camoucore](https://github.com/CamouBrowser/camoucore), which the Camoufox
launcher shares. The GeoIP database and the fpgen model are therefore one copy
per machine, whichever launcher fetched them.

| Requirement | Value |
|---|---|
| Python | 3.11 or newer |
| Playwright | below 1.63 (installed as a dependency) |

## Use

```python
from camouwolf.sync_api import Camouwolf

with Camouwolf(os="windows", headless="virtual", humanize=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

`camouwolf.async_api.AsyncCamouwolf` is the same with `async with`.
`NewBrowser(playwright, ...)` / `AsyncNewBrowser` launch on a Playwright
instance you own, and `launch_options(...)` returns the options for
`playwright.chromium.launch()` (pass them back as `from_options=`).

### Options

| Option | What it does |
|---|---|
| `os` | `"windows"`, `"macos"`, `"linux"`, or a list. Default: what the host can pass for (its own OS; on Linux also Windows). Another pairing raises `UnsupportedOSSpoof` unless `allow_os_mismatch=True`. |
| `screen`, `window` | Constrain the drawn screen (`camouwolf.fingerprints.Screen`); fix the outer window size. |
| `fingerprint` | An fpgen Chrome fingerprint to use instead of drawing one. |
| `webgl_config` | A `(vendor, renderer)` pair fpgen has recorded from Chrome on `os`; the identity is drawn from a machine with that GPU. |
| `fingerprint_preset` | A device recorded from real Chrome, in Camoufox's preset format (user agent, cores, touch points, screen, GPU, timezone); the rest is drawn from a machine fpgen recorded with that GPU. |
| `cpu` | `{"arch": "x86" \| "arm", "manufacturer": "intel" \| "amd" \| "apple" \| "qualcomm"}`, either key optional. Narrows the drawn machine: the GPU it comes with, the OS, and `userAgentData`'s architecture; a CPU no machine has with the GPU raises `ValueError`. |
| `config` | Config keys set directly; they win over drawn values. An unknown key raises `UnknownProperty`. |
| `locale` | The UI and `Intl` locale (`--lang`), `navigator.languages`, and the `Accept-Language` header. One locale claims Chrome's default list for it (`de-DE` is `de-DE, de, en-US, en`); a non-persistent context serves that default for the first of a longer list, as Chrome does off the record. The identity is drawn from a machine set up in that language where fpgen records one, so its voices and fonts follow it; with `geoip`, the place's language does the same. |
| `geoip`, `proxy` | `geoip=True` (or an IP) sets the timezone, locale and geolocation from the exit IP. A SOCKS5 proxy's `username` and `password` go to the browser in its config, not on the command line. Without a proxy, the browser behaves as on an IPv4-only network unless this host's IPv6 address geolocates to the same place, so a site reached over IPv6 cannot see an address elsewhere. |
| `headless` | `True` (Chromium headless), `False`, `"virtual"` (headed on Xvfb), `"virtual-gpu"` (headed on a headless Xorg bound to `gpu`; `require_gpu`). |
| `humanize` | `True`, or a dict of [myuri](https://pypi.org/project/myuri/) engines per channel. Runs in the driver, as in Camoufox. |
| `fonts`, `custom_fonts_only` | Extra font families to report installed; or only those. |
| `addons`, `exclude_addons` | Unpacked extension directories; `DefaultAddons.UBO` is uBlock Origin Lite. Extensions run only with `persistent_context=True`: Chromium runs none in the incognito-like contexts of a non-persistent launch (the launch warns). |
| `block_images`, `block_webrtc`, `block_webgl`, `enable_cache` | As named. `block_webrtc` keeps WebRTC to TCP through the proxy. Without it, a proxied launch's WebRTC looks like a machine's at the proxy's exit: UDP goes through a SOCKS5 proxy's relay and through no other proxy, and with `geoip` its address is the exit's. |
| `instant_animations`, `show_cursor` | Finish every finite animation at once (a page reads the zero duration back, so it warns); show a ripple where automation clicks, in the overlay a page cannot read. |
| `disable_coop`, `allow_addon_new_tab` | Ignore `Cross-Origin-Opener-Policy` (warns); let extensions open tabs and windows of their own, which they cannot by default. |
| `config={"certificates": [...]}`, `config={"certificatePaths": [...]}` | CA certificates (base64 DER, or PEM or DER files) trusted for this launch, for a proxy that terminates TLS. |
| `chromium_prefs` | Merged into the profile's `Preferences`; needs `persistent_context=True` and `user_data_dir`. |
| `chrome_version` | Claim another Chromium milestone than the browser's (warns). |
| `canvas_noise` | Seeded canvas readback noise. Off by default, since stock Chrome has none. |
| `pin_cpu_cores` | Pin the browser to as many cores as the identity reports. |
| `args`, `env` | Extra switches and environment. |
| `executable_path` | A specific build; `CAMOUWOLF_EXECUTABLE_PATH` does the same. |
| `persistent_context`, `from_options`, `debug`, `i_know_what_im_doing` | As in Camoufox. `debug` also sends Chromium's log to stderr, and prints the config with proxy passwords masked. |

### How the identity reaches the browser

The identity is JSON in the environment variables `CAMOU_CONFIG_1..N`, never on
the command line: argv is readable in `/proc/<pid>/cmdline` and Chromium copies
much of it to its renderers. The keys and their types are in
[`src/camouwolf/data/properties.json`](src/camouwolf/data/properties.json).
Keys marked `"carriedBy": "launcher"` are not read by the browser: the launcher
turns them into `--window-size` / `--window-position` and the context's
geolocation.

`camouwolf.switches` audits the command line: automation markers
(`--enable-automation`) and switches a page can measure (`--disable-web-security`)
are dropped, and a switch that would contradict the identity (`--user-agent`,
`--lang`, `--window-size`, ...) raises, naming the option to use instead.
`camouwolf.clienthints` derives the user agent and every `Sec-CH-UA-*` value
from one identity and refuses a launch where they disagree.

### Contexts

A Camouwolf browser has one identity per process. `new_context()` and
`new_page()` default to `no_viewport=True` and to the host's own media features
(Playwright's defaults are emulation overrides). `NewContext()` accepts
`proxy`, `geolocation`, `timezone_id` and `locale`, and refuses options that
would give one context another identity (`user_agent`, `screen`, ...).
A context with its own `proxy` is placed where that proxy exits: `timezone_id`
and `geolocation` default to the exit IP's, looked up through the proxy (needs
the `geoip` extra), unless both are given, and a failed lookup raises
`InvalidIP`. Its locale stays the identity's.

### Differences from Camoufox

| Camoufox | Camouwolf |
|---|---|
| `firefox_user_prefs` | Raises `NotImplementedError`; use `args`, or `chromium_prefs` with a persistent context. |
| `ff_version` | Raises `NotImplementedError`; use `chrome_version`. |
| `fingerprint_preset=True` picks a bundled Firefox preset | Raises `ValueError`: no Chrome presets are bundled. A preset dict works. |
| `cpu` | As in Camoufox, except that a Linux identity has no `arm` CPU: fpgen records no Chrome on Linux ARM. |
| `showcursor` draws the pointer | `show_cursor` shows where clicks land, not where the pointer is: Chromium's DevTools click visualization. |
| `memorysaver` | Not ported: Camoufox removed it. |
| `main_world_eval` | Accepted, nothing to enable: Playwright evaluates in the main world on Chromium, and an isolated-world `evaluate` is not available. |
| `addons` take `.xpi`-style extracted Firefox addons; uBlock Origin is bundled | Unpacked Chrome extensions; uBlock Origin Lite, installed by `camouwolf fetch`. |
| `NewContext()` draws an identity per context | One identity per browser. |
| SOCKS5 proxy authentication | Supported (RFC 1929), for page traffic and WebRTC's UDP relay. A per-context `proxy` takes no SOCKS5 password. |
| Xvfb screen `1x1` | The identity's screen in device pixels (screen × `devicePixelRatio`): the browser's real window renders at that ratio. `CAMOUWOLF_VIRTUAL_DISPLAY_SIZE` fixes the size instead, and the screen is then kept within it. |

## CLI

```
camouwolf fetch [version]     install the browser, fpgen model, GeoIP data, default addon
camouwolf sync                refresh the list of available releases
camouwolf set [specifier]     choose a channel or pin a build (--geoip for the GeoIP source)
camouwolf active | list | remove | version | path
camouwolf test [url]          open a browser with the Playwright inspector
camouwolf server              launch a Playwright server and print its endpoint
camouwolf gpu-setup | gpu-xorg-config [gpu]
```

A client that connects to `camouwolf server` gets Playwright's context defaults;
pass `no_viewport=True` to `new_context()` there.

## Tests

```bash
python -m pytest python/tests -q
python python/tests/manual/smoke_kraft.py    # needs a browser binary
```

## License

MIT. Derived from the Camoufox Python launcher by daijro and the Camoufox
contributors; see [LICENSE](LICENSE).
