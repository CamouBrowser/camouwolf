#!/usr/bin/env python3
"""Record which font families each persona can resolve from the pinned bundle.

The launcher claims an identity's recorded fonts only where the bundle can
render them: a claimed family that does not render is caught by one
measureText call. This script writes that list,
python/src/camouwolf/data/bundle-fonts.json, from the extracted bundle
(browser/scripts/fetch-fonts.py --extract), the same way the browser resolves
a family: through the persona's own fonts.conf, pointed at the groups that
persona reads.

A family resolves when fontconfig lists it (after the persona's rejectfont
rules), or when the persona aliases it with a strong or same binding to a
family that resolves: Skia accepts such a substitution as the requested family.

The bundle is camoufox's, byte for byte, so --check also fails when camoufox's
own pin (browser/scripts/data/font-bundle.json on its main branch) has moved on.

Usage:
    python3 scripts/gen-bundle-fonts.py           # regenerate the JSON
    python3 scripts/gen-bundle-fonts.py --check   # fail if the JSON or the pin is stale
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "browser" / "bundle"
FONTS = BUNDLE / "fonts"
SPEC = REPO / "browser" / "scripts" / "data" / "font-bundle.json"
OUT = REPO / "python" / "src" / "camouwolf" / "data" / "bundle-fonts.json"
PERSONAS = {"lin": "linux", "mac": "macos", "win": "windows"}
DIR_LITERAL = '<dir prefix="cwd">fonts</dir>'
CACHE_LITERAL = '<cachedir prefix="xdg">fontconfig</cachedir>'
CAMOUFOX_SPEC = "https://raw.githubusercontent.com/{repo}/main/browser/scripts/data/font-bundle.json"


def group_dirs(os_key: str) -> list[Path]:
    """The bundle groups a persona reads, as the launcher's _font_scan_dirs picks them."""
    read_by = json.loads((FONTS / "groups.json").read_text(encoding="utf-8"))["readBy"][os_key]
    return [FONTS / g for g in read_by if (FONTS / g).is_dir()]


def persona_conf(os_key: str, cache: Path) -> str:
    raw = (BUNDLE / "fontconfig" / PERSONAS[os_key] / "fonts.conf").read_text(encoding="utf-8")
    dirs = "\n\t".join(f"<dir>{d}</dir>" for d in group_dirs(os_key))
    return raw.replace(DIR_LITERAL, dirs).replace(CACHE_LITERAL, f"<cachedir>{cache}</cachedir>")


def strong_aliases(conf: str) -> dict[str, list[str]]:
    """family -> the families a pattern rule substitutes for it with a strong/same binding."""
    out: dict[str, list[str]] = {}
    for match in ET.fromstring(conf).iter("match"):
        if match.get("target", "pattern") != "pattern":
            continue
        tests = [s.text.strip() for t in match.findall("test") if t.get("name") == "family"
                 for s in t.findall("string") if s.text]
        targets = [s.text.strip() for e in match.findall("edit")
                   if e.get("name") == "family" and e.get("binding") in ("strong", "same")
                   and e.get("mode") in ("assign", "assign_replace", "prepend", "prepend_first")
                   for s in e.findall("string") if s.text]
        for name in tests:
            out.setdefault(name, []).extend(targets)
    return out


def resolvable(os_key: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        conf = persona_conf(os_key, Path(tmp) / "cache")
        conf_path = Path(tmp) / "fonts.conf"
        conf_path.write_text(conf, encoding="utf-8")
        listed = subprocess.run(
            ["fc-list", "--format", "%{family}\n"],
            env={"FONTCONFIG_FILE": str(conf_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, check=True,
        ).stdout
    families = {name.strip() for line in listed.splitlines() for name in line.split(",") if name.strip()}
    lower = {f.lower() for f in families}
    aliases = strong_aliases(conf)
    # Chains (a -> b -> c) settle within a few passes.
    for _ in range(len(aliases)):
        added = {src for src, dsts in aliases.items()
                 if src.lower() not in lower and any(d.lower() in lower for d in dsts)}
        if not added:
            break
        families |= added
        lower |= {a.lower() for a in added}
    # Generic keywords are not families a page can probe.
    generic = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "mono", "sans", "sans serif"}
    return sorted((f for f in families if f.lower() not in generic), key=lambda f: (f.lower(), f))


def build() -> dict:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    stamp = FONTS / ".bundle-sha256"
    if not stamp.is_file() or stamp.read_text(encoding="utf-8").strip() != spec["sha256"]:
        sys.exit("bundle/fonts/ is not the pinned bundle: run browser/scripts/fetch-fonts.py --extract")
    if not shutil.which("fc-list"):
        sys.exit("fc-list (fontconfig) is needed")
    return {
        "bundle": {"asset": spec["asset"], "sha256": spec["sha256"]},
        "families": {key: resolvable(key) for key in PERSONAS},
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="fail if the JSON names another bundle, or (with the bundle extracted) is stale")
    args = ap.parse_args()
    if args.check:
        recorded = json.loads(OUT.read_text(encoding="utf-8"))
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        pinned = spec["sha256"]
        with urllib.request.urlopen(CAMOUFOX_SPEC.format(repo=spec["repo"]), timeout=30) as resp:
            camoufox = json.loads(resp.read())
        if camoufox["sha256"] != pinned:
            print(f"camoufox now pins {camoufox['asset']} ({camoufox['sha256']}); copy its font-bundle.json "
                  "to browser/scripts/data/, re-extract and regenerate", file=sys.stderr)
            return 1
        if recorded["bundle"]["sha256"] != pinned:
            print(f"{OUT.relative_to(REPO)} was generated from another bundle; "
                  "run fetch-fonts.py --extract, then this script", file=sys.stderr)
            return 1
        if (FONTS / "groups.json").is_file():
            if render(build()) != OUT.read_text(encoding="utf-8"):
                print(f"{OUT.relative_to(REPO)} is stale; regenerate it", file=sys.stderr)
                return 1
        print("bundle-fonts.json matches the pinned bundle")
        return 0
    OUT.write_text(render(build()), encoding="utf-8")
    data = json.loads(OUT.read_text(encoding="utf-8"))
    print(f"wrote {OUT.relative_to(REPO)}: " + ", ".join(f"{k} {len(v)}" for k, v in data["families"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
