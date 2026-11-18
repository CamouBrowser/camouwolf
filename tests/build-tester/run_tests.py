#!/usr/bin/env python3
"""Build-tester driver: launch identities through the launcher and check them.

For each profile this launches the browser the way a user's script does
(`camouwolf.launch_options()`), opens a page served from a loopback HTTP
server, and compares three things with each other:

  * what the launcher **claimed** (the config it put in the environment),
  * what the **page** sees (main frame, an iframe, a dedicated worker), and
  * what the **network** saw (request headers of the navigation, a fetch, and
    a worker's fetch).

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

from ci.browser import claimed_config, launcher_options, require_private_display  # noqa: E402

PAGE = HERE / "page"
Check = Dict[str, Any]
Results = Dict[str, Dict[str, Check]]

# A second identity per profile index, so one run covers more than one locale
# and timezone. Index 0 is the launcher's own default draw.
VARIANTS: List[Dict[str, Any]] = [
    {},
    {"locale": "de-DE", "config": {"timezone": "Europe/Berlin"}},
    {"locale": "ja-JP", "config": {"timezone": "Asia/Tokyo"}},
    {"locale": "en-GB", "config": {"timezone": "America/Sao_Paulo"}},
]

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


def offset_minutes(zone: str, when: dt.datetime) -> int:
    """What Date.prototype.getTimezoneOffset() returns in `zone` at `when`."""
    return -int(when.astimezone(ZoneInfo(zone)).utcoffset().total_seconds() // 60)


def compare(claimed: Dict[str, Any], os_name: str, got: Dict[str, Any], probe: Dict[str, Any],
            navigation: Dict[str, str]) -> Results:
    out: Results = {}
    nav, uad, worker = got["navigator"], got.get("uad") or {}, got.get("worker") or {}
    high = uad.get("high") or {}

    # ---- Navigator ---------------------------------------------------------
    c = out["Navigator"] = {}
    for prop in ("userAgent", "platform", "hardwareConcurrency", "deviceMemory", "maxTouchPoints"):
        c[prop] = same(claimed, f"navigator.{prop}", nav.get(prop))
    # Languages are only claimed when a locale was asked for; otherwise the
    # browser's own default stands and there is nothing to compare it with.
    c["languages"] = (same(claimed, "navigator.languages", nav.get("languages")) if "navigator.languages" in claimed
                      else check(bool(nav.get("languages")), f"navigator.languages = {nav.get('languages')}"))
    c["webdriver"] = check(nav.get("webdriver") is False, f"navigator.webdriver = {nav.get('webdriver')!r}")
    ua = str(nav.get("userAgent"))
    token = {"windows": "Windows NT 10.0; Win64; x64", "linux": "X11; Linux x86_64", "macos": "Macintosh; Intel Mac OS X 10_15_7"}[os_name]
    c["userAgentNamesTheOs"] = check(f"({token})" in ua, f"{os_name} identity, UA {ua}")
    platform = {"windows": "Win32", "linux": "Linux x86_64", "macos": "MacIntel"}[os_name]
    c["platformNamesTheOs"] = check(nav.get("platform") == platform, f"{os_name} identity, platform {nav.get('platform')!r}")
    c["touchMatchesMaxTouchPoints"] = check(
        bool(got["media"]["touchEvents"]) == bool(nav.get("maxTouchPoints")),
        f"maxTouchPoints {nav.get('maxTouchPoints')}, ontouchstart {got['media']['touchEvents']}")

    # ---- Client Hints: JS, and the same identity on the wire ---------------
    c = out["Client Hints"] = {}
    if not uad:
        c["userAgentData"] = check(False, "navigator.userAgentData is missing")
    else:
        c["platform"] = same(claimed, "userAgentData.platform", uad.get("platform"))
        c["mobile"] = same(claimed, "userAgentData.mobile", uad.get("mobile"))
        for prop in ("platformVersion", "architecture", "bitness", "model", "uaFullVersion", "wow64"):
            c[prop] = same(claimed, f"userAgentData.{prop}", high.get(prop))
        for prop, actual in (("brands", uad.get("brands")), ("fullVersionList", high.get("fullVersionList"))):
            key = f"userAgentData.{prop}"
            c[prop] = (check(brand_set(claimed[key]) == brand_set(actual), f"claimed {claimed[key]}, saw {actual}")
                       if key in claimed else check(False, f"the launcher claimed no {key}; saw {actual}"))
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
                           ("workerFetch", worker.get("fetchHeaders") or {})):
        if not headers or "error" in headers:
            c[f"{label}Headers"] = check(False, f"no request headers recorded for the {label}: {headers}")
            continue
        c[f"{label}UserAgentHeader"] = check(headers.get("user-agent") == ua, f"header {headers.get('user-agent')!r}, navigator {ua!r}")
        c[f"{label}NoHeadlessOnTheWire"] = check("headless" not in json.dumps(headers).lower(), "a header names HeadlessChrome")
        accept = [part.split(";")[0].strip() for part in (headers.get("accept-language") or "").split(",") if part.strip()]
        c[f"{label}AcceptLanguage"] = check(accept == list(nav.get("languages") or []),
                                            f"header {headers.get('accept-language')!r}, navigator.languages {nav.get('languages')}")
        if label == "workerFetch":
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
    if "error" in worker or not worker:
        c["workerRan"] = check(False, worker.get("error", "no worker result"))
    else:
        # No `webdriver`: WorkerNavigator does not have it.
        for prop in ("userAgent", "platform", "hardwareConcurrency", "deviceMemory", "languages"):
            c[prop] = check(worker.get(prop) == nav.get(prop), f"page {nav.get(prop)!r}, worker {worker.get(prop)!r}")
        c["timeZone"] = check(worker.get("timeZone") == got["intl"]["timeZone"], f"page {got['intl']['timeZone']}, worker {worker.get('timeZone')}")
        c["locale"] = check(worker.get("locale") == got["intl"]["locale"], f"page {got['intl']['locale']}, worker {worker.get('locale')}")
        c["userAgentData"] = check(json.dumps(worker.get("uad"), sort_keys=True) == json.dumps(uad, sort_keys=True),
                                   f"page {uad}, worker {worker.get('uad')}")
        page_gl = (got["webgl"].get("webgl") or {})
        worker_gl = worker.get("webgl") or {}
        c["webgl"] = check(worker_gl.get("vendor") == page_gl.get("vendor") and worker_gl.get("renderer") == page_gl.get("renderer"),
                           f"page {page_gl.get('vendor')} / {page_gl.get('renderer')}, worker {worker_gl}")

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
    renderer = str((got["webgl"].get("webgl") or {}).get("renderer"))
    c["rendererNamesTheOs"] = check(
        ("Direct3D" in renderer) == (os_name == "windows") and ("Metal" in renderer or "Apple" in renderer) == (os_name == "macos"),
        f"{os_name} identity, renderer {renderer}")

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

    # ---- Other surfaces the config names -------------------------------------
    c = out["Other Surfaces"] = {}
    c["canvasStable"] = check(got["canvas"][0] == got["canvas"][1], f"two reads of one drawing differ: {got['canvas']}")
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
    if "webGpu:vendor" in claimed:
        # Its own category: an adapter needs a GPU, which a CI runner on a
        # software renderer does not have (ci/build-tester.yml).
        c = out["WebGPU"] = {}
        gpu = got.get("webgpu")
        c["adapterInfo"] = check(isinstance(gpu, dict) and gpu.get("vendor") == claimed["webGpu:vendor"]
                                   and gpu.get("architecture") == claimed.get("webGpu:architecture"),
                                   f"claimed {claimed['webGpu:vendor']}/{claimed.get('webGpu:architecture')}, saw {gpu}")
    return out


def font_families(claimed: Dict[str, Any]) -> Dict[str, str]:
    """Claimed font name -> the CSS family it belongs to.

    The list mixes families and full names ("Arial", "Arial Bold"). A full name
    resolves as itself or through its family, so both are probed.
    """
    return {str(name): _STYLE_WORDS.sub("", str(name)).strip() or str(name) for name in claimed.get("fonts") or []}


# ---------------------------------------------------------------------------
# one profile
# ---------------------------------------------------------------------------


def run_profile(playwright: Any, binary: Path, server: Server, os_name: str, index: int, headless: bool,
                log: Callable[[str], None]) -> Dict[str, Any]:
    variant = VARIANTS[index % len(VARIANTS)]
    name = f"{os_name}-{index}"
    profile: Dict[str, Any] = {"name": name, "os": os_name, "variant": variant, "results": {}}
    try:
        options = launcher_options(binary, os_name=os_name, headless=headless, **json.loads(json.dumps(variant)))
        claimed = claimed_config(options)
    except Exception as exc:  # noqa: BLE001
        profile["results"] = {"Launch": {"launcherOptions": check(False, f"{type(exc).__name__}: {exc}")}}
        return profile
    profile["claimed_keys"] = sorted(claimed)
    browser = None
    try:
        server.seen.pop("navigation", None)
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(no_viewport=True)
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
        profile["results"] = {"Launch": {"launched": check(True)}, **result["checks"],
                              **compare(claimed, os_name, result["collected"], probe, server.seen.get("navigation") or {})}
        profile["identity"] = {
            "userAgent": result["collected"]["navigator"]["userAgent"],
            "renderer": (result["collected"]["webgl"].get("webgl") or {}).get("renderer"),
            "screen": f"{result['collected']['screen']['width']}x{result['collected']['screen']['height']}",
            "timeZone": result["collected"]["intl"]["timeZone"],
            "canvas": result["collected"]["canvas"][0],
        }
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
