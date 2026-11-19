#!/usr/bin/env python3
"""Cross-check the config keys of the browser, the schema and the launcher.

The launcher and the browser are joined by strings: the launcher serializes an
identity into CAMOU_CONFIG_1..N, and C++ reads keys back out of it. Nothing
compiles the two against each other, so a key one side writes and the other
never reads is silent: the browser starts, the launch succeeds, and the surface
is stock. This script joins the three places a key lives:

  READ      keys the C++ reads through CamouwolfConfig, in browser/patches
            (added lines only) and browser/additions
  SCHEMA    browser/settings/properties.json, the list the launcher validates a
            config against; python/src/camouwolf/data/properties.json must be
            byte-identical to it. An entry marked "carriedBy": "launcher" is
            not a browser key: the launcher turns it into a switch or a context
            option.
  WRITTEN   keys the launcher names: python/src/camouwolf/{fpgen.yml,
            clienthints.py, fingerprints.py, utils.py, webgl.py} and the
            installed camoucore's locales.py

and fails on:

  * the two properties.json copies differing
  * a browser key in SCHEMA that no C++ reads (the launcher would send it and
    nothing would happen)
  * a key C++ reads that SCHEMA does not list (the launcher refuses to send it,
    so the reader is dead code)
  * a launcher-carried key that C++ reads, or that the launcher never names
  * a key the launcher names that is in neither SCHEMA nor LAUNCHER_INTERNAL

A browser key the launcher never names is reported, not failed: it is settable
through `config=` only.

Run:  python3 scripts/check-config-keys.py [--list]
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
BROWSER = REPO / "browser"
LAUNCHER = REPO / "python" / "src" / "camouwolf"
SCHEMA = BROWSER / "settings" / "properties.json"
SCHEMA_COPY = LAUNCHER / "data" / "properties.json"

def _core() -> pathlib.Path:
    """The launcher core's package directory. The locale keys are written there."""
    spec = importlib.util.find_spec("camoucore")
    if spec is None or spec.origin is None:
        sys.exit("camoucore is not installed: pip install the python/ launcher first")
    return pathlib.Path(spec.origin).parent


LAUNCHER_FILES = [
    *(LAUNCHER / name for name in ("fpgen.yml", "clienthints.py", "fingerprints.py", "utils.py", "webgl.py")),
    _core() / "locales.py",
]

# Keys the launcher uses between its own modules and removes before the config
# is sent. Each names where it is consumed. Anything the launcher names that is
# neither here nor in the schema is a bug, not a style choice.
LAUNCHER_INTERNAL = {
    "locale:all": "locales.py -> utils.py: becomes navigator.languages and --lang",
    "locale:language": "locales.py -> utils.py: becomes navigator.languages and --lang",
    "locale:region": "locales.py -> utils.py: becomes navigator.languages and --lang",
    "locale:script": "locales.py -> utils.py: locale normalization",
    "window.innerWidth": "fingerprints.py: clamped beside outerWidth, dropped before the config is sent",
    "window.innerHeight": "fingerprints.py: clamped beside outerHeight, dropped before the config is sent",
}

# What a config key looks like: a dotted or colon-separated name in one of the
# schema's domains. Without the domain test the scan also picks up data that
# merely resembles a key ("Tamil:ta:local" in a voice list).
_DOTTED = ("navigator", "screen", "window", "userAgentData", "connection", "storage", "keyboard")
_COLONED = (
    "locale", "geolocation", "canvas", "fonts", "webGl", "webGl2", "webGpu", "webrtc",
    "mediaDevices", "voices", "AudioContext", "battery",
)
_HEAD = r"(?:(?:%s)\.|(?:%s):)" % ("|".join(_DOTTED), "|".join(_COLONED))
KEY_RE = re.compile(r"^%s[A-Za-z][A-Za-z0-9_]*$" % _HEAD)
# The literal head of an f-string that builds a key: f"screen.avail{axis}".
PREFIX_RE = re.compile(r"^%s[A-Za-z0-9_]*$" % _HEAD)

# Every accessor of CamouwolfConfig (browser/additions/camouwolfcfg/camouwolf_config.h).
READ_RE = re.compile(
    r"\b(?:Get(?:String|Int|Uint32|Double|Bool|StringList|StringListLower|IntRect|Nested|List|Dict)"
    r"|Has)\(\s*\"([^\"]+)\""
)


def schema() -> tuple[set[str], set[str]]:
    """(browser keys, launcher-carried keys) from properties.json."""
    entries = json.loads(SCHEMA.read_text())
    carried = {e["property"] for e in entries if e.get("carriedBy") == "launcher"}
    return {e["property"] for e in entries} - carried, carried


def read_keys() -> dict[str, set[str]]:
    """Keys the browser reads through CamouwolfConfig, with the files that do."""
    found: dict[str, set[str]] = {}

    def scan(text: str, rel: str) -> None:
        for key in READ_RE.findall(text):
            found.setdefault(key, set()).add(rel)

    for path in sorted((BROWSER / "patches").glob("*.patch")):
        # Added lines only: a line a patch removes, or its context, is not code
        # this build runs.
        added = "\n".join(
            line[1:] for line in path.read_text(errors="replace").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        scan(added, str(path.relative_to(REPO)))
    for path in sorted((BROWSER / "additions").rglob("*")):
        if path.is_file() and path.suffix in (".cc", ".h", ".mm", ".cpp"):
            scan(path.read_text(errors="replace"), str(path.relative_to(REPO)))
    # The accessor's own name for the environment variable, not a key.
    found.pop("CAMOU_CONFIG", None)
    return found


def _literals(tree: ast.AST):
    """(string, is_prefix) for every string literal and f-string head in a module."""
    inside_fstring = {
        id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in inside_fstring:
                yield node.value, False
        elif isinstance(node, ast.JoinedStr) and len(node.values) > 1:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                yield head.value, True


def written_keys(known: set[str]) -> dict[str, set[str]]:
    """Keys the launcher names, with the files that do.

    A plain string counts when it is a known key or has a key's shape. An
    f-string (`f'screen.avail{axis}'`) counts for every known key it can
    produce, and as itself, with a `*`, when it can produce none.
    """
    found: dict[str, set[str]] = {}
    for path in LAUNCHER_FILES:
        rel = str(path.relative_to(REPO)) if path.is_relative_to(REPO) else f"camoucore/{path.name}"
        if path.suffix == ".yml":
            # Mapping values, without a YAML dependency: `  field: some.key`.
            for line in path.read_text().splitlines():
                value = line.split("#", 1)[0].partition(":")[2].strip().strip("'\"")
                if value in known or KEY_RE.match(value):
                    found.setdefault(value, set()).add(rel)
            continue
        for text, is_prefix in _literals(ast.parse(path.read_text())):
            if is_prefix:
                if not PREFIX_RE.match(text):
                    continue
                produced = [k for k in known if k.startswith(text)]
                for key in produced or [text + "*"]:
                    found.setdefault(key, set()).add(rel)
            elif text in known or KEY_RE.match(text):
                found.setdefault(text, set()).add(rel)
    return found


def main() -> int:
    browser_keys, carried = schema()
    read = read_keys()
    written = written_keys(browser_keys | carried | set(LAUNCHER_INTERNAL))

    if "--list" in sys.argv:
        for key in sorted(browser_keys | carried | set(read) | set(written)):
            state = (
                "carried" if key in carried
                else "internal" if key in LAUNCHER_INTERNAL
                else "browser" if key in browser_keys
                else "UNKNOWN"
            )
            print(f"  {state:8} {'read' if key in read else '    '} "
                  f"{'written' if key in written else '       '}  {key}")
        return 0

    problems: list[tuple[str, list[str]]] = []

    if SCHEMA.read_bytes() != SCHEMA_COPY.read_bytes():
        problems.append((
            "the two properties.json copies differ",
            [f"{SCHEMA.relative_to(REPO)} != {SCHEMA_COPY.relative_to(REPO)}",
             "The launcher validates against its copy and the build ships the other."],
        ))

    unread = sorted(browser_keys - set(read))
    if unread:
        problems.append((
            "in properties.json, read by no C++ (the launcher sends it and nothing happens)",
            unread + ["Give it a reader in browser/patches, mark it \"carriedBy\": \"launcher\", "
                      "or remove it from the schema."],
        ))

    undeclared = sorted(set(read) - browser_keys - carried)
    if undeclared:
        problems.append((
            "read by C++, not in properties.json (the launcher refuses to send it)",
            [f"{key}  ({', '.join(sorted(read[key]))})" for key in undeclared],
        ))

    carried_read = sorted(carried & set(read))
    if carried_read:
        problems.append((
            "marked \"carriedBy\": \"launcher\" but read by C++",
            [f"{key}  ({', '.join(sorted(read[key]))})" for key in carried_read],
        ))

    carried_unnamed = sorted(carried - set(written))
    if carried_unnamed:
        problems.append((
            "marked \"carriedBy\": \"launcher\" but the launcher never names it",
            carried_unnamed,
        ))

    unknown = sorted(set(written) - browser_keys - carried - set(LAUNCHER_INTERNAL))
    if unknown:
        problems.append((
            "named by the launcher, in neither properties.json nor LAUNCHER_INTERNAL",
            [f"{key}  ({', '.join(sorted(written[key]))})" for key in unknown],
        ))

    stale = sorted(k for k in LAUNCHER_INTERNAL if k not in written)
    if stale:
        problems.append((
            "listed in LAUNCHER_INTERNAL but the launcher no longer names it",
            stale + ["Remove it from LAUNCHER_INTERNAL in scripts/check-config-keys.py."],
        ))

    config_only = sorted(browser_keys - set(written))
    print(f"== config keys: {len(read)} read by the browser, {len(browser_keys)} browser keys and "
          f"{len(carried)} launcher-carried in the schema, {len(written)} named by the launcher")
    if config_only:
        print(f"   {len(config_only)} browser key(s) the launcher never names (config= only): "
              + ", ".join(config_only))

    for title, lines in problems:
        print(f"\n-- {title}", file=sys.stderr)
        for line in lines:
            print(f"   {line}", file=sys.stderr)
    if problems:
        return 1
    print("\nEvery key is accounted for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
