#!/usr/bin/env python3
"""Fail when the launcher writes a CAMOU_CONFIG key nothing consumes.

Every entry in docs/HOLO-PARITY.md section 1 was this bug: `src/` computed a
value, serialized it into CAMOU_CONFIG, and no `CamouwolfConfig::Get*()` call
anywhere read it back. The launcher believed it had landed an identity it had
not. `fonts` sat dead long enough that every Camouwolf browser claiming Windows
reported a byte-identical font list.

Nothing caught it because the two halves live in different languages and are
joined by a string. This joins them again.

A key is CONSUMED if any of these is true:
  * a patch or addition reads it through CamouwolfConfig (any Get* accessor, or
    GetNested with it as the domain);
  * it is listed in CARRIED_BY_LAUNCHER below, meaning the launcher delivers it
    some other way and the C++ side is not expected to read it.

Run:  python3 scripts/check-config-keys.py [--list]
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"
BROWSER = REPO / "browser"

# Keys the launcher writes and the browser is NOT expected to read, each with
# the mechanism that actually delivers them. Anything not here and not read by
# a patch is a bug, not a style choice.
CARRIED_BY_LAUNCHER = {
    # Resolved into navigator.languages, the Accept-Language header, and the
    # font/voice draws. Never read as a key.
    "locale:all": "launcher-internal: drives navigator.languages + fonts/voices",
    "locale:language": "launcher-internal: font and voice selection",
    "locale:region": "launcher-internal: font and voice selection",
    "locale:script": "launcher-internal: locale normalization",
    # Delivered through Playwright's context geolocation + permission grant.
    # See applyGeolocationDefaults in src/utils.ts.
    "geolocation:latitude": "Playwright context geolocation",
    "geolocation:longitude": "Playwright context geolocation",
    "geolocation:accuracy": "Playwright context geolocation",
    # The real window is sized and positioned from these (--window-size /
    # --window-position), so Chromium derives the getters from the real rect.
    "window.outerWidth": "--window-size",
    "window.outerHeight": "--window-size",
    "window.innerWidth": "derived from the real window",
    "window.innerHeight": "derived from the real window",
    "window.screenX": "--window-position",
    "window.screenY": "--window-position",
}

# Keys written by the launcher that are known-dead and not yet fixed. Listing
# one here is a deliberate, reviewed decision with a reason — it is not a place
# to silence a new orphan.
KNOWN_DEAD = {
    "battery:charging": "no reader; a headless Linux box already reports the plugged-in-desktop shape",
    "battery:chargingTime": "see battery:charging",
    "battery:dischargingTime": "see battery:charging",
    "navigator.globalPrivacyControl": "no reader",
    "screen.pageXOffset": "no reader",
    "screen.pageYOffset": "no reader",
    "fonts:spacing_seed": "no reader; 0010 covers glyph metrics via the visual seed instead",
    "webrtc:ipv4": "no reader; block_webrtc is the only WebRTC control today",
}

WRITE_RE = re.compile(r"""config\[\s*["']([^"']+)["']\s*\]""")
MAP_RE = re.compile(r"""["']([a-zA-Z][a-zA-Z0-9_]*[.:][a-zA-Z0-9_.:]+)["']\s*,?\s*$""")
# A config key always sits in one of these domains. Without this the mapping
# scan below also picks up data rows that merely LOOK like keys — the voice
# manifests are full of "Tamil:ta:local" records, which are values, not keys.
CONFIG_DOMAIN_RE = re.compile(
    r"^(navigator|screen|window|userAgentData|connection|storage|keyboard)\.|"
    r"^(locale|geolocation|battery|canvas|fonts|webGl|webGl2|webGpu|webrtc|"
    r"mediaDevices|voices|AudioContext):"
)
READ_RE = re.compile(
    r"""Get(?:String|Int|Uint32|Double|Bool|StringList|StringListLower|IntRect|Nested|List|Dict)\(\s*"([^"]+)"""
)
HAS_RE = re.compile(r'(?:Has|CheckBool)\(\s*"([^"]+)"')


def written_keys() -> dict[str, set[str]]:
    """Keys src/ assigns into a config object, with the files that do it."""
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.ts")):
        text = path.read_text()
        rel = str(path.relative_to(REPO))
        for match in WRITE_RE.finditer(text):
            found.setdefault(match.group(1), set()).add(rel)
        # mappings/*.config.ts express the same thing as a value in a table:
        #     charging: "battery:charging",
        if path.name == "browserforge.config.ts":
            for line in text.splitlines():
                m = MAP_RE.search(line.strip())
                if m and CONFIG_DOMAIN_RE.search(m.group(1)):
                    found.setdefault(m.group(1), set()).add(rel)
    return found


def read_keys() -> set[str]:
    """Keys the browser side reads back through CamouwolfConfig."""
    found: set[str] = set()
    for root in (BROWSER / "patches", BROWSER / "additions"):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            found.update(READ_RE.findall(text))
            found.update(HAS_RE.findall(text))
    return found


def main() -> int:
    written = written_keys()
    read = read_keys()

    if "--list" in sys.argv:
        for key in sorted(written):
            state = (
                "read"
                if key in read
                else "launcher"
                if key in CARRIED_BY_LAUNCHER
                else "DEAD"
                if key in KNOWN_DEAD
                else "ORPHAN"
            )
            print(f"  {state:9} {key}")
        return 0

    orphans = {
        key: files
        for key, files in written.items()
        if key not in read
        and key not in CARRIED_BY_LAUNCHER
        and key not in KNOWN_DEAD
    }

    # A key listed as dead that someone has since implemented should be moved
    # out of the list, so the list keeps meaning something.
    resurrected = sorted(k for k in KNOWN_DEAD if k in read)

    print(f"== config keys: {len(written)} written, {len(read)} read by the browser")
    print(f"   {len(KNOWN_DEAD)} known-dead, {len(CARRIED_BY_LAUNCHER)} carried by the launcher")

    if resurrected:
        print("\n-- now read, but still listed in KNOWN_DEAD:", file=sys.stderr)
        for key in resurrected:
            print(f"   {key}", file=sys.stderr)
        print("   Remove them from KNOWN_DEAD in scripts/check-config-keys.py.", file=sys.stderr)

    if orphans:
        print("\n-- ORPHANED: written by the launcher, read by nothing", file=sys.stderr)
        for key, files in sorted(orphans.items()):
            print(f"   {key}", file=sys.stderr)
            for f in sorted(files):
                print(f"       written in {f}", file=sys.stderr)
        print(
            "\n   Either give it a reader in browser/patches, deliver it another way\n"
            "   and record that in CARRIED_BY_LAUNCHER, or stop writing it. Do not\n"
            "   add it to KNOWN_DEAD to make this pass — that list is for reviewed\n"
            "   decisions, not for new orphans.",
            file=sys.stderr,
        )

    if orphans or resurrected:
        return 1
    print("\nAll written keys are accounted for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
