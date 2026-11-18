#!/usr/bin/env python3
"""Validate the bundled fontconfig personas.

These files decide which fonts a persona can render. They fail SILENTLY and
catastrophically, which is why they get their own gate rather than a code
review:

  * A run of `--` anywhere inside an XML comment makes the whole file
    unparseable. fontconfig does not error -- it falls back to the host's
    defaults for EVERY family, so the persona renders with the build machine's
    fonts and the leak looks like nothing at all. (Cost one build; cost this
    checker's author two.)
  * `generate_fontconfig` (python/src/camouwolf/utils.py) rewrites one exact literal to point the
    persona at an absolute font directory. Reflow that line and the rewrite
    silently no-ops, leaving `prefix="cwd"` to resolve against whatever the
    launcher's working directory happened to be.
  * A catch-all `append_last` family rule must NOT be present. It looks like a
    safety net -- it stops an unmatched request falling through to whatever
    fontconfig sorts first -- but it makes EVERY family resolve, so every font
    a detector probes measures as installed, including the foreign-OS markers
    that give the persona away. Measured: with the rule, Arimo, DejaVu Sans,
    Helvetica Neue and a nonsense control all measured present; without it,
    all four correctly measure absent and the real Windows families still
    resolve. Blink does not need the net -- Skia refuses a non-matching family
    for a specific request and falls through to the CSS-declared generic,
    which is exactly what stock Chrome does.

Usage:
    python3 scripts/check-bundle-fontconfig.py

Exit code is non-zero if anything is wrong, so CI can gate on it.
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[1]
FONTCONFIG = REPO / "browser" / "bundle" / "fontconfig"
PERSONAS = ("linux", "macos", "windows")

# The literal generate_fontconfig() replaces. Must match byte for byte.
DIR_LITERAL = '<dir prefix="cwd">fonts</dir>'

# What a Linux Chromium build asks fontconfig for, whatever persona it wears:
# the values compiled in from locale_settings_linux.grd. Every persona must
# resolve all five -- either by aliasing them or by really carrying them.
CHROMIUM_LINUX_PREFS = (
    "Times New Roman",  # standard + serif
    "Arial",            # sans-serif
    "Monospace",        # fixed
    "Comic Sans MS",    # cursive
    "Impact",           # fantasy
)


def aliased_families(root: ET.Element) -> set[str]:
    out = set()
    for match in root.iter("match"):
        for test in match.findall("test"):
            if test.get("name") == "family":
                for s in test.findall("string"):
                    if s.text:
                        out.add(s.text.strip())
    return out


def has_catch_all(root: ET.Element) -> bool:
    """A `<match target="pattern">` that appends a family to EVERY request."""
    for match in root.iter("match"):
        for edit in match.findall("edit"):
            if edit.get("name") == "family" and edit.get("mode") == "append_last":
                return True
    return False


def bundled_families(persona: str) -> set[str] | None:
    """Family names the persona's font dir really carries, if it is restored."""
    fonts = REPO / "browser" / "bundle" / "fonts" / persona
    if not fonts.is_dir() or not any(fonts.iterdir()):
        return None
    import shutil
    import subprocess

    if not shutil.which("fc-scan"):
        return None
    done = subprocess.run(
        ["fc-scan", "--format", "%{family}\n", str(fonts)],
        capture_output=True,
        text=True,
    )
    out = set()
    for line in done.stdout.splitlines():
        for name in line.split(","):
            if name.strip():
                out.add(name.strip())
    return out


def main() -> int:
    problems: list[str] = []
    print(f"== bundled fontconfig personas\n")

    for persona in PERSONAS:
        conf = FONTCONFIG / persona / "fonts.conf"
        label = f"{persona}/fonts.conf"
        if not conf.is_file():
            problems.append(f"{label}: missing")
            print(f"-- {persona}\n   !! missing")
            continue

        raw = conf.read_text()
        notes: list[str] = []

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            # Name the usual culprit rather than just the offset.
            hint = ""
            for i, line in enumerate(raw.splitlines(), 1):
                if "--" in line.replace("<!--", "").replace("-->", ""):
                    hint = f" (line {i} has a bare '--' inside a comment)"
                    break
            problems.append(f"{label}: not well-formed XML: {exc}{hint}")
            print(f"-- {persona}\n   !! not well-formed XML: {exc}{hint}")
            continue

        if DIR_LITERAL not in raw:
            problems.append(
                f"{label}: missing the exact literal {DIR_LITERAL!r} that "
                "generate_fontconfig rewrites"
            )
        else:
            notes.append("dir literal ok")

        if has_catch_all(root):
            problems.append(
                f"{label}: catch-all append_last family rule present -- it makes "
                "every probed family resolve, so foreign-OS markers measure as "
                "installed and the persona leaks"
            )
        else:
            notes.append("no catch-all ok")

        aliased = aliased_families(root)
        carried = bundled_families(persona)
        for pref in CHROMIUM_LINUX_PREFS:
            if pref in aliased:
                continue
            if carried is None:
                notes.append(f"{pref}: unverified (bundle not restored)")
                continue
            if pref not in carried:
                problems.append(
                    f"{label}: Chromium's Linux build asks for {pref!r}, but this "
                    "persona neither aliases it nor carries it"
                )
        if carried is not None:
            notes.append(f"{len(carried)} families in bundle")

        print(f"-- {persona}")
        for n in notes:
            print(f"   {n}")

    print()
    if problems:
        for p in problems:
            print(f"!! {p}")
        print(f"\n!! {len(problems)} problem(s) — the persona would render with the "
              "wrong fonts, silently.")
        return 1
    print("All good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
