#!/usr/bin/env python3
"""Build-tester driver: launch identities through the launcher and check them.

For each profile this launches the browser the way a user's script does
(`camouwolf.launch_options()`), opens a page served from a loopback HTTP
server, and compares three things with each other:

  * what the launcher **claimed** (the config it put in the environment),
  * what the **page** sees (main frame, an iframe, a dedicated, a shared and a
    service worker), and
  * what the **network** saw (request headers of the navigation, a fetch, and
    each worker's fetch).

Each launch also opens a context through `NewContext()`, which draws it an
identity of its own, and checks that context the same way against that
identity. The launch's page is checked after the context exists, so it shows
that a plain context keeps the launch's identity beside it.

`page/checks.js` holds the checks a page can decide alone; the comparisons
that need the claimed values are here. The output is one JSON document:

    {"profiles": [{"name", "os", "results": {category: {check: {passed, detail}}}}]}

A check that could not run is a failed check, with the reason as its detail.

Not run directly in CI: `python -m ci.run_build_tester` starts the private X
display this needs and turns the JSON into a gate result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ci.browser import claimed_config, import_launcher, launcher_options, require_private_display  # noqa: E402

PAGE = HERE / "page"
Check = Dict[str, Any]
Results = Dict[str, Dict[str, Check]]

# A second identity per profile index, so one run covers more than one locale
# and timezone. Index 0 is the launcher's own default draw.
VARIANTS: List[Dict[str, Any]] = [
    {},
    # No voices at launch, so a context's voices can be spoken only by an
    # engine that lists them from the context's identity.
    {"locale": "de-DE", "config": {"timezone": "Europe/Berlin", "voices": []}},
    {"locale": "ja-JP", "config": {"timezone": "Asia/Tokyo"}},
    {"locale": "en-GB", "config": {"timezone": "America/Sao_Paulo"}},
]

# The workers the page starts, each by its own path into a renderer.
WORKERS = ("worker", "sharedWorker", "serviceWorker")

# Families that exist on one OS and not the other: a Windows identity that
# resolves a Linux family (or the reverse) names its host.
FOREIGN_FONTS = {
    "windows": ["DejaVu Sans", "Liberation Sans", "Ubuntu", "Noto Sans CJK JP", "Cantarell"],
    "linux": ["Segoe UI", "Calibri", "Cambria", "Consolas", "Helvetica Neue"],
    "macos": ["Segoe UI", "Calibri", "DejaVu Sans", "Liberation Sans", "Ubuntu"],
}
_STYLE_WORDS = re.compile(
    r"\s+(Bold|Italic|Oblique|Light|Medium|Semibold|SemiBold|Semilight|Black|Thin|Regular|Condensed|"
    r"ExtraLight|ExtraBold|Heavy|Demi|DemiBold|Book|Narrow)\b.*$"
)


# ---------------------------------------------------------------------------
# loopback server
# ---------------------------------------------------------------------------


class Server:
    """Serves page/ and echoes request headers. Loopback only, port chosen by the OS."""

    def __init__(self) -> None:
        seen: Dict[str, Dict[str, str]] = {}
        self.seen = seen

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def do_GET(self) -> None:  # noqa: N802
                path, _, query = self.path.partition("?")
                headers = {key.lower(): value for key, value in self.headers.items()}
                if path == "/headers":
                    body = json.dumps(headers).encode()
                    ctype = "application/json"
                else:
                    name = "index.html" if path == "/" else path.lstrip("/")
                    target = PAGE / name
                    if "/" in name or not target.is_file():
                        self.send_error(404)
                        return
                    if name == "index.html":
                        seen["navigation"] = headers
                    body = target.read_bytes()
                    ctype = "text/html" if name.endswith(".html") else "text/javascript"
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                # Ask for every high-entropy hint, as a site that fingerprints does.
                self.send_header("Accept-CH", "Sec-CH-UA-Platform-Version, Sec-CH-UA-Arch, Sec-CH-UA-Bitness, "
                                 "Sec-CH-UA-Model, Sec-CH-UA-Full-Version-List, Sec-CH-UA-WoW64")
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


# ---------------------------------------------------------------------------
# comparisons
# ---------------------------------------------------------------------------


def check(passed: bool, detail: Any = "") -> Check:
    return {"passed": bool(passed), "detail": "" if passed and not detail else str(detail)[:600]}


def same(claimed: Dict[str, Any], key: str, actual: Any) -> Check:
    """`actual` equals the claimed value of `key`. An unclaimed key is not a pass."""
    if key not in claimed:
        return check(False, f"the launcher claimed no {key}; saw {actual!r}")
    return check(claimed[key] == actual, f"claimed {claimed[key]!r}, saw {actual!r}")


def parse_brands(header: Optional[str]) -> List[Dict[str, str]]:
    return [{"brand": b.replace('\\"', '"').replace("\\\\", "\\"), "version": v}
            for b, v in re.findall(r'"((?:[^"\\]|\\.)*)";\s*v="([^"]*)"', header or "")]


def unquote(value: Optional[str]) -> Optional[str]:
    return value.strip('"') if isinstance(value, str) else value


def brand_set(brands: Any) -> List[tuple]:
    return sorted((b.get("brand"), b.get("version")) for b in (brands or []))


def served_languages(options: Dict[str, Any]) -> Optional[List[str]]:
    """What an off-the-record context (browser.new_page) serves for the
    --accept-lang the launcher passed: language_prefs.cc GetIncognitoLanguageList,
    which keeps a one-language list and swaps a longer one for Chrome's default
    list of its first language."""
    CHROME_LANGUAGES = import_launcher().utils.CHROME_LANGUAGES  # noqa: N806

    passed = next((arg.split("=", 1)[1].split(",") for arg in options.get("args") or []
                   if arg.startswith("--accept-lang=")), None)
    if not passed or len(passed) == 1:
        return passed
    lists = CHROME_LANGUAGES["lists"]
    return list(lists.get(passed[0]) or [passed[0], *lists[CHROME_LANGUAGES["fallback"]]])


def header_languages(languages: List[str]) -> List[str]:
    """The Accept-Language list Chrome sends for `languages`: net::HttpUtil::
    ExpandLanguageList adds a base language after the last tag of its family."""
    out: List[str] = []
    for i, tag in enumerate(languages):
        base = tag.split("-")[0]
        nxt = languages[i + 1].split("-")[0] if i + 1 < len(languages) else None
        for code in (tag, base) if base not in ("x", "i") and nxt != base else (tag,):
            if code not in out:
                out.append(code)
    return out


def offset_minutes(zone: str, when: dt.datetime) -> int:
    """What Date.prototype.getTimezoneOffset() returns in `zone` at `when`."""
    return -int(when.astimezone(ZoneInfo(zone)).utcoffset().total_seconds() // 60)


def compare(claimed: Dict[str, Any], served: Optional[List[str]], os_name: str, got: Dict[str, Any],
            probe: Dict[str, Any], navigation: Dict[str, str]) -> Results:
    out: Results = {}
    nav, uad = got["navigator"], got.get("uad") or {}
    workers = {kind: got.get(kind) or {} for kind in WORKERS}
    high = uad.get("high") or {}

    # ---- Navigator ---------------------------------------------------------
    c = out["Navigator"] = {}
    for prop in ("userAgent", "platform", "hardwareConcurrency", "deviceMemory", "maxTouchPoints"):
        c[prop] = same(claimed, f"navigator.{prop}", nav.get(prop))
    # The launcher passes a language list only when a locale was asked for;
    # otherwise the browser's own default stands and there is nothing to compare.
    c["languages"] = (same(claimed, "navigator.languages", nav.get("languages")) if "navigator.languages" in claimed
                      else check(nav.get("languages") == served, f"expected {served}, saw {nav.get('languages')}")
                      if served else check(bool(nav.get("languages")), f"navigator.languages = {nav.get('languages')}"))
    c["webdriver"] = check(nav.get("webdriver") is False, f"navigator.webdriver = {nav.get('webdriver')!r}")
    ua = str(nav.get("userAgent"))
    token = {"windows": "Windows NT 10.0; Win64; x64", "linux": "X11; Linux x86_64", "macos": "Macintosh; Intel Mac OS X 10_15_7"}[os_name]
    c["userAgentNamesTheOs"] = check(f"({token})" in ua, f"{os_name} identity, UA {ua}")
    platform = {"windows": "Win32", "linux": "Linux x86_64", "macos": "MacIntel"}[os_name]
    c["platformNamesTheOs"] = check(nav.get("platform") == platform, f"{os_name} identity, platform {nav.get('platform')!r}")
    # A touch laptop's Chrome reports the digitizer and a coarse pointer, but
    # desktop Chrome never exposes ontouchstart, touchscreen or not.
    c["touchMatchesMaxTouchPoints"] = check(
        bool(got["media"]["coarsePointer"]) == bool(nav.get("maxTouchPoints")) and not got["media"]["touchEvents"],
        f"maxTouchPoints {nav.get('maxTouchPoints')}, any-pointer: coarse {got['media']['coarsePointer']}, "
        f"ontouchstart {got['media']['touchEvents']}")
    # Every identity is a desktop driven by a mouse, headless or not.
    c["primaryPointerIsAMouse"] = check(got["media"]["mouse"], "(pointer: fine) and (hover: hover)")

    # ---- Client Hints: JS, and the same identity on the wire ---------------
    c = out["Client Hints"] = {}
    if not uad:
        c["userAgentData"] = check(False, "navigator.userAgentData is missing")
    else:
        c["platform"] = same(claimed, "userAgentData.platform", uad.get("platform"))
        c["mobile"] = same(claimed, "userAgentData.mobile", uad.get("mobile"))
        for prop in ("platformVersion", "architecture", "bitness", "model", "uaFullVersion", "wow64"):
            c[prop] = same(claimed, f"userAgentData.{prop}", high.get(prop))
        # The public build claims Chromium: its own list, Chromium and one GREASE
        # entry, in both forms (ci/tribal-rules.yml, public-build-claims-chromium).
        for prop, actual in (("brands", uad.get("brands")), ("fullVersionList", high.get("fullVersionList"))):
            names = sorted(str(b.get("brand")) for b in actual or [])
            c[prop] = check(len(names) == 2 and "Chromium" in names and any("Not" in n for n in names),
                            f"expected Chromium and one GREASE brand, saw {actual}")
        major = (re.search(r"Chrome/(\d+)\.", ua) or [None, None])[1]
        versions = {b.get("brand"): b.get("version") for b in uad.get("brands") or []}
        c["brandVersionMatchesUserAgent"] = check(
            versions.get("Chromium") == major and all(v == major for b, v in versions.items() if "Not" not in str(b)),
            f"UA says Chrome/{major}, brands {versions}")
        c["fullVersionMatchesUserAgent"] = check(str(high.get("uaFullVersion", "")).split(".")[0] == major,
                                                 f"UA says Chrome/{major}, uaFullVersion {high.get('uaFullVersion')}")
        c["platformMatchesUserAgent"] = check(
            uad.get("platform") == {"windows": "Windows", "linux": "Linux", "macos": "macOS"}[os_name],
            f"{os_name} identity, userAgentData.platform {uad.get('platform')!r}")
    for label, headers in (("navigation", navigation), ("fetch", got.get("fetchHeaders") or {}),
                           *((f"{kind}Fetch", worker.get("fetchHeaders") or {}) for kind, worker in workers.items())):
        if not headers or "error" in headers:
            c[f"{label}Headers"] = check(False, f"no request headers recorded for the {label}: {headers}")
            continue
        c[f"{label}UserAgentHeader"] = check(headers.get("user-agent") == ua, f"header {headers.get('user-agent')!r}, navigator {ua!r}")
        c[f"{label}NoHeadlessOnTheWire"] = check("headless" not in json.dumps(headers).lower(), "a header names HeadlessChrome")
        accept = [part.split(";")[0].strip() for part in (headers.get("accept-language") or "").split(",") if part.strip()]
        c[f"{label}AcceptLanguage"] = check(accept == header_languages(list(nav.get("languages") or [])),
                                            f"header {headers.get('accept-language')!r}, navigator.languages {nav.get('languages')}")
        if label not in ("navigation", "fetch"):
            # Stock Chromium sends no Sec-CH-UA on a dedicated worker's fetch to
            # a loopback origin (checked against 151); demanding it would fail
            # a correct build.
            continue
        c[f"{label}SecChUa"] = check(brand_set(parse_brands(headers.get("sec-ch-ua"))) == brand_set(uad.get("brands")),
                                     f"header {headers.get('sec-ch-ua')!r}, JS {uad.get('brands')}")
        c[f"{label}SecChUaPlatform"] = check(unquote(headers.get("sec-ch-ua-platform")) == uad.get("platform"),
                                             f"header {headers.get('sec-ch-ua-platform')!r}, JS {uad.get('platform')!r}")
        c[f"{label}SecChUaMobile"] = check(headers.get("sec-ch-ua-mobile") == ("?1" if uad.get("mobile") else "?0"),
                                           f"header {headers.get('sec-ch-ua-mobile')!r}")
    fetched = got.get("fetchHeaders") or {}
    if "sec-ch-ua-platform-version" in fetched:
        c["highEntropyHeadersMatchJs"] = check(
            unquote(fetched.get("sec-ch-ua-platform-version")) == high.get("platformVersion")
            and unquote(fetched.get("sec-ch-ua-arch")) == high.get("architecture")
            and unquote(fetched.get("sec-ch-ua-bitness")) == high.get("bitness")
            and brand_set(parse_brands(fetched.get("sec-ch-ua-full-version-list"))) == brand_set(high.get("fullVersionList")),
            f"headers {dict((k, v) for k, v in fetched.items() if k.startswith('sec-ch-ua-'))}, JS {high}")
    else:
        c["highEntropyHeadersMatchJs"] = check(False, "Accept-CH was sent and the fetch carried no Sec-CH-UA-Platform-Version")

    # ---- Worker: the same identity off the main thread ---------------------
    c = out["Worker"] = {}
    for kind, worker in workers.items():
        if "error" in worker or not worker:
            c[f"{kind}Ran"] = check(False, worker.get("error", f"no {kind} result"))
            continue
        # No `webdriver`: WorkerNavigator does not have it.
        for prop in ("userAgent", "platform", "hardwareConcurrency", "deviceMemory", "languages"):
            c[f"{kind}.{prop}"] = check(worker.get(prop) == nav.get(prop), f"page {nav.get(prop)!r}, {kind} {worker.get(prop)!r}")
        c[f"{kind}.timeZone"] = check(worker.get("timeZone") == got["intl"]["timeZone"],
                                      f"page {got['intl']['timeZone']}, {kind} {worker.get('timeZone')}")
        c[f"{kind}.locale"] = check(worker.get("locale") == got["intl"]["locale"], f"page {got['intl']['locale']}, {kind} {worker.get('locale')}")
        c[f"{kind}.userAgentData"] = check(json.dumps(worker.get("uad"), sort_keys=True) == json.dumps(uad, sort_keys=True),
                                           f"page {uad}, {kind} {worker.get('uad')}")
        page_gl = (got["webgl"].get("webgl") or {})
        worker_gl = worker.get("webgl") or {}
        c[f"{kind}.webgl"] = check(worker_gl.get("vendor") == page_gl.get("vendor") and worker_gl.get("renderer") == page_gl.get("renderer"),
                                   f"page {page_gl.get('vendor')} / {page_gl.get('renderer')}, {kind} {worker_gl}")

    # ---- Timezone & Locale --------------------------------------------------
    c = out["Timezone & Locale"] = {}
    intl = got["intl"]
    # With no timezone claimed the host's stands; the page must still agree
    # with itself, so the offsets are checked against the zone Intl names.
    zone = claimed.get("timezone") or intl["timeZone"]
    try:
        ZoneInfo(zone)
    except Exception:  # noqa: BLE001
        c["timeZone"] = check(False, f"{zone!r} is not an IANA zone")
        zone = None
    if zone:
        c["timeZone"] = check(intl["timeZone"] == zone, f"claimed {zone}, Intl reports {intl['timeZone']}")
        now = dt.datetime.fromtimestamp(intl["now"] / 1000, dt.timezone.utc)
        for label, when, actual in (
            ("offsetNow", now, intl["offsetNow"]),
            ("offsetJanuary", dt.datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(zone)), intl["offsetJanuary"]),
            ("offsetJuly", dt.datetime(2026, 7, 15, 12, tzinfo=ZoneInfo(zone)), intl["offsetJuly"]),
        ):
            c[label] = check(offset_minutes(zone, when) == actual, f"{zone} is {offset_minutes(zone, when)} min, Date reports {actual}")
        expected = dt.datetime(2026, 1, 15, 12, tzinfo=dt.timezone.utc).astimezone(ZoneInfo(zone)).strftime("GMT%z")
        c["dateStringOffset"] = check(expected in intl["dateString"], f"expected {expected} in {intl['dateString']!r}")
    languages = list(nav.get("languages") or [])
    first = languages[0] if languages else ""
    if "locale:intl" in claimed:
        c["intlLocale"] = same(claimed, "locale:intl", intl["locale"])
    else:
        # Stock Chromium resolves its UI locale, which may drop the region
        # ("de" for de-DE), so only the language has to agree.
        c["intlLocale"] = check(intl["locale"] == first or first.startswith(intl["locale"] + "-"),
                                f"navigator.languages[0] is {first}, Intl.DateTimeFormat resolves {intl['locale']}")
    c["intlLocalesAgree"] = check(intl["locale"] == intl["numberLocale"] == intl["collatorLocale"],
                                  f"DateTimeFormat {intl['locale']}, NumberFormat {intl['numberLocale']}, Collator {intl['collatorLocale']}")
    c["languageIsFirstLanguage"] = check(bool(languages) and nav.get("language") == languages[0], f"language {nav.get('language')}, languages {languages}")

    # ---- WebGL --------------------------------------------------------------
    c = out["WebGL"] = {}
    for kind, prefix in (("webgl", "webGl"), ("webgl2", "webGl")):
        info = got["webgl"].get(kind)
        if not info:
            c[f"{kind}Context"] = check(False, f"no {kind} context")
            continue
        c[f"{kind}Vendor"] = same(claimed, f"{prefix}:vendor", info.get("vendor"))
        c[f"{kind}Renderer"] = same(claimed, f"{prefix}:renderer", info.get("renderer"))
        c[f"{kind}PlainStrings"] = check(info.get("plainVendor") == "WebKit" and info.get("plainRenderer") == "WebKit WebGL",
                                         f"VENDOR {info.get('plainVendor')!r}, RENDERER {info.get('plainRenderer')!r}")
    for kind, key, probed in (("webgl", "webGl:parameters", "webglParameters"), ("webgl2", "webGl2:parameters", "webgl2Parameters")):
        wanted = claimed.get(key) or (claimed.get("webGl:parameters") if kind == "webgl2" else None) or {}
        bad = [f"{p}: claimed {v}, saw {probe[probed].get(str(p))}" for p, v in wanted.items()
               if probe[probed].get(str(p)) != v and not (kind == "webgl2" and key not in claimed and probe[probed].get(str(p)) is None)]
        c[f"{kind}Parameters"] = check(not bad, "; ".join(bad)) if wanted else check(False, f"the launcher claimed no {key}")
    for kind, prefix in (("webgl", "webGl"), ("webgl2", "webGl2")):
        info = got["webgl"].get(kind) or {}
        if f"{prefix}:supportedExtensions" in claimed:
            # Hide-only: the build reports the claimed list minus what the host lacks.
            extra = sorted(set(info.get("extensions") or []) - set(claimed[f"{prefix}:supportedExtensions"]))
            c[f"{kind}Extensions"] = check(not extra, f"reported but not claimed: {extra}")
        if f"{prefix}:shaderPrecisionFormats" in claimed:
            seen = info.get("shaderPrecisionFormats") or {}
            bad = [f"{k}: claimed {v}, saw {seen.get(k)}" for k, v in claimed[f"{prefix}:shaderPrecisionFormats"].items()
                   if seen.get(k) != v]
            c[f"{kind}ShaderPrecision"] = check(not bad, "; ".join(bad[:6]))
    renderer = str((got["webgl"].get("webgl") or {}).get("renderer"))
    c["rendererNamesTheOs"] = check(
        ("Direct3D" in renderer) == (os_name == "windows") and ("Metal" in renderer or "Apple" in renderer) == (os_name == "macos"),
        f"{os_name} identity, renderer {renderer}")

    # ---- Editing ------------------------------------------------------------
    c = out["Editing"] = {}
    wanted = "alpha " if os_name == "windows" else "alpha"
    c["doubleClickSelection"] = check(probe["doubleClick"] == wanted,
                                      f"{os_name} identity, a double-click selected {probe['doubleClick']!r}")
    if os_name in KEY_BINDINGS:
        c["keyBindings"] = check(probe["keys"] == KEY_BINDINGS[os_name],
                                 f"{os_name} identity, wanted {KEY_BINDINGS[os_name]}, saw {probe['keys']}")

    # ---- Platform APIs ------------------------------------------------------
    # Windows and macOS Chrome ship Web Share and Web Bluetooth; Linux ships
    # neither. A machine without a Bluetooth adapter answers as below.
    c = out["Platform APIs"] = {}
    desktop = os_name in ("windows", "macos")
    c["webShare"] = check(probe["apis"]["share"] == desktop, f"{os_name} identity, navigator.share: {probe['apis']['share']}")
    wanted_bluetooth = {"availability": "false", "requestDevice": "NotFoundError: Bluetooth adapter not available."} if desktop else None
    c["webBluetooth"] = check(probe["apis"]["bluetooth"] == wanted_bluetooth,
                              f"{os_name} identity, wanted {wanted_bluetooth}, saw {probe['apis']['bluetooth']}")

    # ---- Screen & Window ----------------------------------------------------
    c = out["Screen & Window"] = {}
    screen, window = got["screen"], got["window"]
    for prop in ("width", "height", "availWidth", "availHeight", "availLeft", "availTop", "colorDepth", "pixelDepth"):
        c[f"screen.{prop}"] = same(claimed, f"screen.{prop}", screen.get(prop))
    for prop in ("outerWidth", "outerHeight", "screenX", "screenY"):
        c[f"window.{prop}"] = same(claimed, f"window.{prop}", window.get(prop))
    c["availWithinScreen"] = check(screen["availWidth"] <= screen["width"] and screen["availHeight"] <= screen["height"], screen)
    c["windowWithinScreen"] = check(window["outerWidth"] <= screen["width"] and window["outerHeight"] <= screen["height"]
                                    and window["innerWidth"] <= window["outerWidth"] and window["innerHeight"] <= window["outerHeight"],
                                    f"window {window}, screen {screen['width']}x{screen['height']}")
    c["mediaQueriesAgree"] = check(got["media"]["deviceWidth"] and got["media"]["deviceHeight"] and got["media"]["colorDepth"],
                                   f"device-width/height/color media queries vs screen: {got['media']}")
    if "window.devicePixelRatio" in claimed:
        c["devicePixelRatio"] = same(claimed, "window.devicePixelRatio", window.get("devicePixelRatio"))

    # ---- Fonts --------------------------------------------------------------
    c = out["Fonts"] = {}
    families = font_families(claimed)
    if not families:
        c["claimedFontsResolve"] = check(False, "the launcher claimed no fonts")
    else:
        missing = [name for name, family in families.items()
                   if not probe["fonts"].get(name) and not probe["fonts"].get(family)]
        c["claimedFontsResolve"] = check(not missing, f"{len(missing)} of {len(families)} claimed fonts do not resolve: {', '.join(missing[:12])}")
    leaked = [f for f in FOREIGN_FONTS[os_name] if probe["fonts"].get(f)]
    c["noForeignOsFonts"] = check(not leaked, f"a {os_name} identity resolves: {', '.join(leaked)}")

    # ---- Voices -------------------------------------------------------------
    c = out["Voices"] = {}
    seen = [f"{v['name']}|{v['lang']}|{v['voiceURI']}|{int(v['localService'])}|{int(v['default'])}" for v in got.get("voices") or []]
    if "voices" not in claimed:
        # An identity drawn without a speech backend claims nothing; the
        # browser's own list stands and there is nothing to compare it with.
        c["voices"] = check(True, f"none claimed; the page lists {len(seen)}")
    else:
        c["voices"] = check(seen == list(claimed["voices"]),
                            f"claimed {len(claimed['voices'])}, saw {len(seen)}; first difference: "
                            f"{next(((a, b) for a, b in zip(list(claimed['voices']) + [None], seen + [None]) if a != b), None)}")
        for kind, events in probe["speech"].items():
            wanted = expected_speech(os_name, kind == "local")
            seen_events = [{k: e[k] for k in ("type", "name", "charIndex", "charLength") if k in e} for e in events]
            c[f"speak.{kind}"] = check(seen_events == wanted and bool(events) and events[-1]["at"] >= MIN_SPEECH_MS,
                                       f"{os_name} identity, wanted {wanted}, saw {events}")

    # ---- Other surfaces the config names -------------------------------------
    c = out["Other Surfaces"] = {}
    c["canvasStable"] = check(got["canvas"][0] == got["canvas"][1], f"two reads of one drawing differ: {got['canvas']}")
    # Chrome reports usage + 10 GiB whatever the disk (kStaticStorageQuota), so
    # any other quota is a patch or the host showing through.
    storage = got.get("storage") or {}
    c["storageQuota"] = check(isinstance(storage.get("quota"), int) and storage["quota"] - storage.get("usage", 0) == 10 * 2**30,
                              f"estimate() {storage}")
    if "mediaDevices:enumerated" in claimed:
        kinds = [str(entry).split("|")[0] for entry in claimed["mediaDevices:enumerated"]]
        c["mediaDevices"] = check(got.get("mediaDevices") == kinds, f"claimed {kinds}, saw {got.get('mediaDevices')}")
    if "keyboard.layoutMap" in claimed:
        keyboard = got.get("keyboard")
        c["keyboardLayout"] = check(isinstance(keyboard, list) and sorted(keyboard) == sorted(claimed["keyboard.layoutMap"]),
                                    f"claimed {len(claimed['keyboard.layoutMap'])} keys, saw {str(keyboard)[:200]}")
    if claimed.get("systemColors"):
        bad = [f"{k}: claimed {v:#08x}, saw {probe['systemColors'].get(k)}" for k, v in claimed["systemColors"].items()
               if probe["systemColors"].get(k) != v]
        c["systemColors"] = check(not bad, "; ".join(bad[:8]))
    if "webGpu:preferredCanvasFormat" in claimed:
        c["webgpuCanvasFormat"] = same(claimed, "webGpu:preferredCanvasFormat", got.get("preferredCanvasFormat"))
    if "media:platformDecoders" in claimed:
        c = out["Media Codecs"] = {}
        compare_codecs(claimed, got.get("codecs") or {}, c)
    if "battery:level" in claimed:
        battery = got.get("battery") or {}
        full = claimed["battery:charging"] and claimed["battery:level"] >= 1
        expected = {"charging": claimed["battery:charging"], "level": claimed["battery:level"],
                    "chargingTime": "0" if full else "Infinity", "dischargingTime": "Infinity"}
        c = out["Battery"] = {}
        c["getBattery"] = check(battery == expected, f"claimed {expected}, saw {battery}")
    if "webGpu:vendor" in claimed:
        # Its own category: an adapter needs a GPU, which a CI runner on a
        # software renderer does not have (ci/build-tester.yml).
        c = out["WebGPU"] = {}
        gpu = got.get("webgpu")
        c["adapterInfo"] = check(isinstance(gpu, dict) and gpu.get("vendor") == claimed["webGpu:vendor"]
                                   and gpu.get("architecture") == claimed.get("webGpu:architecture"),
                                   f"claimed {claimed['webGpu:vendor']}/{claimed.get('webGpu:architecture')}, saw {gpu}")
        if "webGpu:subgroupMinSize" in claimed:
            wanted = [claimed["webGpu:subgroupMinSize"], claimed.get("webGpu:subgroupMaxSize")]
            sizes = [gpu.get("subgroupMinSize"), gpu.get("subgroupMaxSize")] if isinstance(gpu, dict) else gpu
            c["subgroupSizes"] = check(sizes == wanted, f"claimed {wanted}, saw {sizes}")
    return out


def compare_codecs(claimed: Dict[str, Any], got: Dict[str, Any], c: Dict[str, Check]) -> None:
    """Every surface that names a platform codec answers with the claimed one."""
    decoders, encoders = set(claimed["media:platformDecoders"]), set(claimed["media:platformEncoders"])

    def agree(name: str, seen: Dict[str, Any], claim: set) -> None:
        expected = {codec: codec in claim for codec in seen}
        c[name] = check(seen == expected and bool(seen), f"claimed {sorted(claim)}, saw {seen}")

    agree("webCodecsHardwareDecode", got.get("hardwareDecode") or {}, decoders)
    agree("decodingInfoPowerEfficient", got.get("powerEfficient") or {}, decoders)
    agree("webCodecsHardwareEncode", got.get("hardwareEncode") or {}, encoders)
    hevc = "hevc" in decoders
    c["hevcPlayback"] = check(got.get("hevcPlayback") == {"canPlayType": "probably" if hevc else "", "mse": hevc},
                              f"HEVC decoder claimed {hevc}, saw {got.get('hevcPlayback')}")
    aac = "aac" in encoders
    c["aacEncode"] = check(got.get("aacEncode") == {"audioEncoder": aac, "mediaRecorder": aac},
                           f"AAC encoder claimed {aac}, saw {got.get('aacEncode')}")
    c["hevcRecord"] = check(got.get("hevcRecord") == ("hevc" in encoders),
                            f"HEVC encoder claimed {'hevc' in encoders}, saw {got.get('hevcRecord')}")
    rtc = got.get("rtc") or {}
    for name, key, side in (("rtcSendFormats", "media:rtcEncodeFormats", "send"),
                            ("rtcReceiveFormats", "media:rtcDecodeFormats", "receive")):
        missing = [entry for entry in claimed[key] if entry not in (rtc.get(side) or [])]
        # Formats only a hardware codec offers: H264 High, and H265.
        extra = [entry for entry in rtc.get(side) or [] if entry not in claimed[key]
                 and (entry.startswith("H265|") or "profile-level-id=64" in entry)]
        c[name] = check(isinstance(rtc.get(side), list) and not missing and not extra,
                        f"missing {missing}, unclaimed {extra}")


def font_families(claimed: Dict[str, Any]) -> Dict[str, str]:
    """Claimed font name -> the CSS family it belongs to.

    The list mixes families and full names ("Arial", "Arial Bold"). A full name
    resolves as itself or through its family, so both are probed.
    """
    return {str(name): _STYLE_WORDS.sub("", str(name)).strip() or str(name) for name in claimed.get("fonts") or []}


# ---------------------------------------------------------------------------
# one profile
# ---------------------------------------------------------------------------


# What the claimed OS's key bindings do in a textarea: Windows has Alt+Backspace
# undo and no Ctrl+Shift+Backspace; Linux the reverse.
KEY_BINDINGS = {
    "windows": {"ctrlShiftBackspace": "one two", "altBackspace": ""},
    "linux": {"ctrlShiftBackspace": "", "altBackspace": "one two"},
}


def key_bindings(page: Any) -> Dict[str, str]:
    """The textarea's value after each binding, typed into a fresh textarea."""
    out = {}
    for name, key in (("ctrlShiftBackspace", "Control+Shift+Backspace"), ("altBackspace", "Alt+Backspace")):
        page.evaluate("""() => {
            document.getElementById('keys')?.remove();
            const t = document.createElement('textarea');
            t.id = 'keys';
            document.body.prepend(t);
            t.focus();
        }""")
        page.keyboard.type("one two")
        page.keyboard.press(key)
        out[name] = page.evaluate("document.getElementById('keys').value")
    return out


def platform_apis(page: Any) -> Dict[str, Any]:
    """Web Share's presence, and Web Bluetooth's answers when present."""
    return page.evaluate("""async () => {
        const settle = (p) => Promise.race([
            p.then((v) => String(v), (e) => `${e.name}: ${e.message}`),
            new Promise((resolve) => setTimeout(() => resolve('still pending after 5s'), 5000)),
        ]);
        return {
            share: typeof navigator.share === 'function',
            bluetooth: navigator.bluetooth ? {
                availability: await settle(navigator.bluetooth.getAvailability()),
                requestDevice: await settle(navigator.bluetooth.requestDevice({acceptAllDevices: true})),
            } : null,
        };
    }""")


SPEECH_TEXT = "Hello there. A short test."
# A real engine takes about 60ms a character; an utterance that ends sooner
# than this was not spoken at the claimed OS's pace.
MIN_SPEECH_MS = 600


def expected_speech(os_name: str, local: bool) -> List[Dict[str, Any]]:
    """The events speak() fires: a local voice reports words (macOS, Windows)
    and sentences (Windows); a network voice reports neither."""
    events: List[Dict[str, Any]] = [{"type": "start"}]
    sentence_start = True
    for word in re.finditer(r"\S+", SPEECH_TEXT):
        if local and os_name == "windows" and sentence_start:
            events.append({"type": "boundary", "name": "sentence", "charIndex": word.start()})
        if local and os_name in ("windows", "macos"):
            events.append({"type": "boundary", "name": "word", "charIndex": word.start(), "charLength": len(word.group())})
        sentence_start = word.group()[-1] in ".!?"
    return events + [{"type": "end"}]


def speech(page: Any, claimed: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """The events of speaking SPEECH_TEXT with the identity's first local and
    first network voice."""
    out = {}
    for kind, local in (("local", "1"), ("network", "0")):
        voice = next((v.split("|")[0] for v in claimed.get("voices") or [] if v.split("|")[3:4] == [local]), None)
        if voice is None:
            continue
        out[kind] = page.evaluate("""([name, text]) => new Promise((resolve) => {
            const u = new SpeechSynthesisUtterance(text);
            u.voice = speechSynthesis.getVoices().find((v) => v.name === name);
            const events = [];
            const t0 = performance.now();
            const log = (e) => {
                const event = {type: e.type, at: Math.round(performance.now() - t0)};
                if (e.type === 'boundary') {
                    event.name = e.name;
                    event.charIndex = e.charIndex;
                    if (e.name === 'word') event.charLength = e.charLength;
                }
                if (e.type === 'error') event.error = e.error;
                events.push(event);
                if (e.type === 'end' || e.type === 'error') resolve(events);
            };
            for (const type of ['start', 'boundary', 'end', 'error']) u.addEventListener(type, log);
            setTimeout(() => resolve(events), 15000);
            speechSynthesis.speak(u);
        })""", [voice, SPEECH_TEXT])
    return out


def double_click_selection(page: Any) -> str:
    """What a double-click on a word selects: Windows takes the trailing space."""
    page.evaluate("""() => {
        const p = document.createElement('p');
        p.id = 'editing';
        p.style.font = '32px monospace';
        p.textContent = 'alpha beta';
        document.body.prepend(p);
    }""")
    box = page.locator("#editing").bounding_box()
    page.mouse.dblclick(box["x"] + 20, box["y"] + box["height"] / 2)
    return page.evaluate("String(getSelection())")


def check_page(page: Any, server: Server, claimed: Dict[str, Any], served: Optional[List[str]],
               os_name: str) -> tuple:
    """Load the page in `page` and compare it with `claimed`: the results, and
    the identity the page read."""
    server.seen.pop("navigation", None)
    page.goto(server.url)
    page.wait_for_selector("#result", state="attached", timeout=60_000)
    result = json.loads(page.evaluate("document.getElementById('result').textContent"))
    if "error" in result:
        raise RuntimeError(f"the page's checks threw: {result['error']}")
    params = sorted({*(claimed.get("webGl:parameters") or {}), *(claimed.get("webGl2:parameters") or {})})
    probe = page.evaluate("request => buildTester.probe(request)", {
        "webglParameters": params, "webgl2Parameters": params,
        "fonts": sorted({*font_families(claimed), *font_families(claimed).values(), *FOREIGN_FONTS[os_name]}),
        "systemColors": sorted(claimed.get("systemColors") or {}),
    })
    probe["doubleClick"] = double_click_selection(page)
    probe["keys"] = key_bindings(page)
    probe["apis"] = platform_apis(page)
    probe["speech"] = speech(page, claimed)
    results = {**result["checks"], **compare(claimed, served, os_name, result["collected"], probe,
                                             server.seen.get("navigation") or {})}
    collected = result["collected"]
    identity = {
        "userAgent": collected["navigator"]["userAgent"],
        "renderer": (collected["webgl"].get("webgl") or {}).get("renderer"),
        "screen": f"{collected['screen']['width']}x{collected['screen']['height']}",
        "timeZone": collected["intl"]["timeZone"],
        "canvas": collected["canvas"][0],
    }
    return results, identity


def new_context(browser: Any, variant: Dict[str, Any]) -> tuple:
    """A context NewContext draws its own identity for, with that identity and
    its language list, as the browser was handed them."""
    launcher = import_launcher()
    sync_api = launcher.sync_api
    handed: List[tuple] = []
    params = sync_api.set_context_identity_params

    def record(context_id: str, identity: Dict[str, Any], languages: List[str]) -> Dict[str, Any]:
        handed.append((identity, languages))
        return params(context_id, identity, languages)

    sync_api.set_context_identity_params = record
    try:
        context = launcher.NewContext(browser, i_know_what_im_doing=True, **json.loads(json.dumps(variant)))
    finally:
        sync_api.set_context_identity_params = params
    (identity, languages), = handed
    return context, identity, languages


def run_profile(playwright: Any, binary: Path, server: Server, os_name: str, index: int, headless: bool,
                log: Callable[[str], None]) -> Dict[str, Any]:
    variant = VARIANTS[index % len(VARIANTS)]
    # Another place than the launch's, so the context cannot pass on the launch's values.
    context_variant = VARIANTS[(index + 2) % len(VARIANTS)]
    name = f"{os_name}-{index}"
    profile: Dict[str, Any] = {"name": name, "os": os_name, "variant": variant, "context_variant": context_variant,
                               "results": {}}
    try:
        options = launcher_options(binary, os_name=os_name, headless=headless, **json.loads(json.dumps(variant)))
        claimed = claimed_config(options)
    except Exception as exc:  # noqa: BLE001
        profile["results"] = {"Launch": {"launcherOptions": check(False, f"{type(exc).__name__}: {exc}")}}
        return profile
    profile["claimed_keys"] = sorted(claimed)
    if os_name != "macos":
        # The other touchscreen than the launch's, so a pointer stated for the
        # whole browser cannot pass for the context's (no Mac has one).
        touch = 0 if claimed.get("navigator.maxTouchPoints") else 10
        context_variant = {**context_variant, "config": {**context_variant.get("config", {}),
                                                         "navigator.maxTouchPoints": touch}}
        profile["context_variant"] = context_variant
    browser = None
    try:
        browser = import_launcher().NewBrowser(playwright, from_options=options)
        context, identity, languages = new_context(browser, context_variant)
        launched, profile["identity"] = check_page(browser.new_page(no_viewport=True), server, claimed,
                                                   served_languages(options), os_name)
        own, profile["context_identity"] = check_page(context.new_page(), server, identity, languages, os_name)
        profile["results"] = {"Launch": {"launched": check(True)}, **launched}
        for category, checks in own.items():
            profile["results"].setdefault(category, {}).update({f"context:{n}": c for n, c in checks.items()})
        # Two draws of a whole machine never land on one GPU, font list,
        # canvas seed and voice list together.
        place = ("timezone", "locale:", "geolocation:", "webrtc:")
        drawn = {k for k in {*identity, *claimed} if not k.startswith(place) and identity.get(k) != claimed.get(k)}
        profile["results"]["Launch"]["contextIsAnotherMachine"] = check(
            bool(drawn) and profile["identity"] != profile["context_identity"],
            f"{len(drawn)} keys differ; launch page {profile['identity']}, context page {profile['context_identity']}")
    except Exception as exc:  # noqa: BLE001
        profile["results"].setdefault("Launch", {})["launched"] = check(False, f"{type(exc).__name__}: {str(exc)[:500]}")
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
    failed = sum(1 for cat in profile["results"].values() for c in cat.values() if not c["passed"])
    total = sum(len(cat) for cat in profile["results"].values())
    log(f"{name}: {total - failed}/{total} checks passed")
    return profile


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--os", dest="os_names", nargs="+", default=["linux", "windows"], choices=["linux", "windows", "macos"])
    parser.add_argument("--profiles", type=int, default=4, help="identities per OS")
    parser.add_argument("--headed", action="store_true", help="a headed browser, still on the private display")
    args = parser.parse_args(argv)

    require_private_display()
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    def log(message: str) -> None:
        print(f"[build-tester] {message}", file=sys.stderr, flush=True)

    server = Server()
    profiles: List[Dict[str, Any]] = []
    try:
        with sync_playwright() as playwright:
            for os_name in args.os_names:
                for index in range(args.profiles):
                    profiles.append(run_profile(playwright, args.binary, server, os_name, index, not args.headed, log))
    finally:
        server.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"schema": 1, "headless": not args.headed, "profiles": profiles}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
