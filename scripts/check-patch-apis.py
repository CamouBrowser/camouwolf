#!/usr/bin/env python3
"""Check the patch series for API usage that will not compile.

`check-dead-patches.py` proves each hunk still ANCHORS where it thinks it does.
This proves the code inside those hunks still CALLS things that exist. They are
different failures: a patch can apply perfectly and then fail the compile three
hours later, which is the most expensive way to learn about a renamed method.

Three checks:

1. **Banned spellings.** APIs that exist under a different name, or that
   Chromium has since forbidden. Every entry here is a real break this project
   hit — see the table in docs/PATCHES.md.

2. **Added includes resolve.** Every `+#include "..."` a patch introduces must
   name a file that exists at the pinned tag. Catches a moved or misremembered
   header path, which otherwise surfaces as a wall of unrelated errors.

3. **Runtime feature gating.** An edit to runtime_enabled_features.json5 is not
   code, so checks 1 and 2 never see it — but it rewrites blink's generated
   public API and can break files no patch touches. Catches a feature demoted
   to `public: false` that upstream still sets, and a feature the series treats
   as write-only that has grown a reader.

Both run against the local mirror (`browser/.pristine/<version>/`) where
possible and fall back to querying googlesource for headers the mirror does not
carry. Results are cached so a re-run is instant.

Usage:
    python3 scripts/check-patch-apis.py [--offline]

Exit code is non-zero if anything is wrong, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
PATCHES = REPO / "browser" / "patches"
ADDITIONS = REPO / "browser" / "additions"
CACHE = REPO / "browser" / ".pristine" / ".include-cache.json"

# ---------------------------------------------------------------------------
# 1. banned spellings
# ---------------------------------------------------------------------------
# (regex, what to use instead). Each was a real compile break.
BANNED: list[tuple[str, str]] = [
    (
        r"\bString::FromUTF8\b",
        "String::FromUtf8 — Chromium spells it with lowercase 'tf8'. "
        "Upstream's own media_devices.cc uses FromUtf8.",
    ),
    (
        r"\.Split\([^)]*allow_empty_entries",
        "Vector<String> Split(UChar) — Split no longer takes an out-parameter; "
        "it RETURNS the vector, and keeps empty entries by default.",
    ),
    (
        r"base::span\(\s*static_cast<",
        "a safe span helper (e.g. gfx::SkPixmapToWritableSpan) — "
        "span(ptr, count) is UNSAFE_BUFFER_USAGE in modern Chromium. "
        "If a raw pointer is genuinely unavoidable, wrap it in UNSAFE_BUFFERS().",
    ),
    (
        r"\bValue::(Dict|List)\b",
        "base::DictValue / base::ListValue — Chromium 152 hoisted these out of "
        "Value to namespace scope and left NO compatibility alias. "
        "FindDict()/FindList()/TakeDict() return the new types.",
    ),
    (
        r"DEFINE_THREAD_SAFE_STATIC_LOCAL\([^)]*String[^)]*CamouwolfConfig",
        "assignment in the constructor — caching a spoofed value in a function "
        "local static pins the FIRST object's value for the process lifetime.",
    ),
    (
        r"SetIcuTimeZoneAndNotifyV8\([^)]*\);\s*\n\s*return;\s*\n\s*\}\s*\n\s*if \(!base::FeatureList",
        "icu::TimeZone::adoptDefault() — the notify path walks the scheduler, "
        "pages and worker threads, none of which exist during construction.",
    ),
]


# Symbols that only compile when a particular header is included. A plain
# "banned spelling" rule cannot express this — it would fire whether or not the
# include is present, and a rule that fires on correct code is worse than none.
REQUIRES_INCLUDE: list[tuple[str, str, str, str]] = [
    (
        "NoDestructor",
        r"\bNoDestructor<",
        "base/no_destructor.h",
        "without the real template in scope, `friend class NoDestructor<X>` "
        "parses as declaring a NON-template class, and the genuine definition "
        "then collides with it",
    ),
    (
        "DictValue/ListValue",
        r"\b(DictValue|ListValue)\b",
        "base/values.h",
        "DictValue/ListValue are namespace-scope types in base/values.h as of "
        "Chromium 152",
    ),
]

COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def strip_comments(text: str) -> str:
    """Blank out comments before matching.

    Without this, a comment explaining *why* an API was renamed trips the very
    rule that documents it — which is how the first version of this checker
    reported four failures against code that was already correct.
    """
    return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def pinned_version() -> str:
    text = (REPO / "browser" / "upstream.sh").read_text()
    match = re.search(r"^version=(.+)$", text, re.M)
    if not match:
        raise SystemExit("no version= in browser/upstream.sh")
    return match.group(1).strip()


def added_lines(patch_text: str) -> list[tuple[int, str]]:
    """Lines a patch ADDS, with their position in the patch file."""
    out = []
    for i, line in enumerate(patch_text.splitlines(), start=1):
        if line.startswith("+") and not line.startswith("+++"):
            out.append((i, line[1:]))
    return out


def sources_to_scan() -> list[tuple[str, str]]:
    """(label, text) for everything that ends up compiled.

    Patches contribute only their ADDED lines; additions contribute whole files.
    The additions matter: they are shipped verbatim rather than as diffs, so a
    patch-only scan cannot see them — which is precisely how the Value::Dict
    rename reached the compiler instead of this checker.
    """
    out: list[tuple[str, str]] = []
    for patch in sorted(PATCHES.glob("*.patch")):
        text = patch.read_text()
        out.append((patch.name, "\n".join(line for _, line in added_lines(text))))
    if ADDITIONS.is_dir():
        for src in sorted(ADDITIONS.rglob("*")):
            if src.suffix in {".h", ".cc", ".cpp"}:
                out.append((str(src.relative_to(REPO)), src.read_text()))
    return out


def check_banned() -> list[str]:
    problems = []
    for label, raw in sources_to_scan():
        text = strip_comments(raw)
        for pattern, fix in BANNED:
            for match in re.finditer(pattern, text):
                snippet = match.group(0).replace("\n", " ")[:60]
                problems.append(f"{label}: `{snippet}` -> use {fix}")
        # Paired rules apply only to whole files (an addition), where the
        # include set is knowable. A patch hunk shows only added lines, so the
        # file's existing includes are invisible and a verdict would be a guess.
        if not label.endswith(".patch"):
            # A .cc that includes its own header inherits that header's
            # includes; checking the .cc in isolation would flag correct code.
            visible = text
            own_header = pathlib.Path(label).with_suffix(".h")
            if (REPO / own_header).exists() and own_header.name in text:
                visible += strip_comments((REPO / own_header).read_text())
            for name, pattern, header, why in REQUIRES_INCLUDE:
                if re.search(pattern, text) and f'#include "{header}"' not in visible:
                    problems.append(
                        f"{label}: uses {name} without #include \"{header}\" "
                        f"(directly or via its own header) — {why}"
                    )
    return problems


# ---------------------------------------------------------------------------
# 2. added includes resolve
# ---------------------------------------------------------------------------
INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"')

# Paths that are generated at build time and so exist in no checkout.
GENERATED_PREFIXES = (
    "third_party/blink/renderer/bindings/",
    "mojo/public/",
)
GENERATED_SUFFIXES = ("-blink.h", ".mojom-shared.h", "-inl.h", ".pb.h")


class Unverifiable(RuntimeError):
    """googlesource gave no answer, which is not the same as "no such file"."""


def googlesource(path: str, version: str) -> str | None:
    """A file's text at the pinned tag, or None when googlesource says 404.

    Only a 404 means absent. googlesource throttles shared CI runners, and
    reading that 429 as "absent" failed real includes and cached the wrong
    answer. A 429 or 5xx is retried with backoff, then raised.
    """
    url = (
        "https://chromium.googlesource.com/chromium/src/+/refs/tags/"
        f"{version}/{path}?format=TEXT"
    )
    reason = ""
    for attempt in range(5):
        wait = 0
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return base64.b64decode(resp.read()).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code != 429 and e.code < 500:
                raise Unverifiable(f"{path}: HTTP {e.code}") from e
            reason = f"HTTP {e.code}"
            retry_after = (e.headers or {}).get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else 0
        except urllib.error.URLError as e:
            reason = str(e.reason)
        except TimeoutError as e:
            # A timeout reading the response is raised bare, not as a URLError.
            reason = str(e)
        time.sleep(max(wait, 5 * 2**attempt))
    raise Unverifiable(f"{path}: {reason} after 5 attempts")


def deps_managed_roots(version: str, cache: dict, offline: bool) -> set[str]:
    """Directories chromium/src pulls in from OTHER repos via DEPS.

    A header under one of these (skia, angle, dawn, v8, ...) is real but is NOT
    in the chromium/src tree at any tag, so querying googlesource for it returns
    404. Without this, every such include is a false failure — and a gate that
    cries wolf is a gate people learn to ignore.

    Parsed from the real DEPS file rather than hardcoded, so it stays correct
    across bumps.
    """
    key = "__deps_roots__"
    if key in cache:
        return set(cache[key])
    if offline:
        return set()

    deps = googlesource("DEPS", version)
    if deps is None:
        raise Unverifiable(f"DEPS: 404 at {version}")
    roots: set[str] = set()
    # DEPS keys look like  'src/third_party/skia': { ... }  — take the path.
    for match in re.finditer(r"['\"]src/([^'\"]+)['\"]\s*:", deps):
        roots.add(match.group(1).rstrip("/"))
    cache[key] = sorted(roots)
    return roots


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))


def exists_upstream(path: str, version: str, cache: dict, offline: bool) -> bool | None:
    """True/False, or None when it could not be determined."""
    if path in cache:
        return cache[path]

    mirror = REPO / "browser" / ".pristine" / version / path
    if mirror.exists():
        cache[path] = True
        return True

    if offline:
        return None

    ok = googlesource(path, version) is not None
    cache[path] = ok
    return ok


def check_includes(
    version: str, offline: bool, src: pathlib.Path | None
) -> tuple[list[str], int, int]:
    problems: list[str] = []
    cache = load_cache()
    checked = skipped = 0
    deps_roots = deps_managed_roots(version, cache, offline)

    # Files an additions tree installs. camouwolf<lib> mirrors third_party/<lib>,
    # whose sources include through their own include dirs, so any trailing part
    # of the path names the file.
    provided = set()
    for d in ADDITIONS.iterdir():
        for f in d.rglob("*") if d.is_dir() else ():
            parts = f.relative_to(d).parts
            provided.update("/".join(parts[i:]) for i in range(len(parts)))
            if d.name.startswith("camouwolf"):
                provided.add(f"third_party/{d.name.removeprefix('camouwolf')}/" + "/".join(parts))

    for patch in sorted(PATCHES.glob("*.patch")):
        target = ""
        for line in patch.read_text().splitlines():
            if line.startswith("+++ "):
                target = line[4:].removeprefix("b/")
                continue
            if not line.startswith("+"):
                continue
            match = INCLUDE_RE.match(line[1:])
            if not match:
                continue
            path = match.group(1)

            # Ours, by definition not upstream yet.
            if "camouwolf" in path or path in provided:
                continue
            # Skia and V8 include from their own roots.
            for root in ("third_party/skia/", "v8/"):
                if target.startswith(root) and not path.startswith(("third_party/", "v8/")):
                    path = root + path
            if path.startswith(GENERATED_PREFIXES) or path.endswith(GENERATED_SUFFIXES):
                skipped += 1
                continue
            # A mojom binding header is generated from the .mojom beside it,
            # which is what a checkout or googlesource has.
            lookup = path.removesuffix(".h") if path.endswith(".mojom.h") else path

            # A real checkout is the authoritative answer when we have one: it
            # contains the DEPS-managed repos too.
            if src is not None:
                if (src / lookup).exists():
                    checked += 1
                else:
                    problems.append(
                        f"{patch.name}: #include \"{path}\" not found in {src}"
                    )
                continue

            # No checkout. A header under a DEPS-managed root lives in another
            # repo entirely, so googlesource's chromium/src does not have it and
            # a 404 would be meaningless.
            if any(lookup == r or lookup.startswith(r + "/") for r in deps_roots) and not (
                REPO / "browser" / ".pristine" / version / lookup
            ).exists():
                skipped += 1
                continue

            verdict = exists_upstream(lookup, version, cache, offline)
            if verdict is None:
                skipped += 1
            elif verdict:
                checked += 1
            else:
                problems.append(
                    f"{patch.name}: #include \"{path}\" does not exist at {version}"
                )

    save_cache(cache)
    return problems, checked, skipped


# ---------------------------------------------------------------------------
# 3. runtime feature gating
# ---------------------------------------------------------------------------
# runtime_enabled_features.json5 is not code, so checks 1 and 2 never look at
# it — but an edit there rewrites blink's generated public API, and that breaks
# the compile of files the series does not touch and the mirror does not carry.
#
# This cost a full build day: 0017 set `public: false` on AutomationControlled,
# which stops codegen emitting WebRuntimeFeatures::EnableAutomationControlled().
# Upstream content/child/runtime_features.cc calls it unconditionally, so the
# build died ~6 hours in, at 68%, on a file no patch mentions.
RUNTIME_FEATURES_JSON5 = (
    "third_party/blink/renderer/platform/runtime_enabled_features.json5"
)

# Where a real caller/reader of a feature could live. Blink's own renderer/ is
# excluded deliberately: the series edits it, so a reader there is ours.
FEATURE_SCAN_ROOTS = ("content", "chrome", "components", "third_party/blink/public")

# Features the series leaves settable but relies on NOTHING reading, because a
# patch severed the one consumer. That is a real defence, but a silent one — it
# holds only as long as the reader set stays empty, and a bump can refill it
# without touching a single line we own.
WRITE_ONLY_FEATURES: list[tuple[str, str]] = [
    (
        "AutomationControlled",
        "0002 makes Navigator::webdriver() return false without consulting it, "
        "which is what lets 0017 leave the bit settable-but-unread. A reader "
        "reappearing makes --enable-automation web-visible again -- re-check "
        "the surface before shipping.",
    ),
]


def json5_entries(text: str) -> list[tuple[str, str]]:
    """(name, body) for each `{ ... }` feature entry, by brace matching.

    Field order is NOT reliable -- ElementCapture and RegionCapture both put
    `public:` before `name:` -- so nearest-preceding-name would misattribute.
    """
    out = []
    for match in re.finditer(r"^    \{$", text, re.M):
        depth, i, n = 0, match.start(), len(text)
        while i < n:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[match.start() : i + 1]
        name = re.search(r'^\s*name:\s*"([A-Za-z0-9_]+)"', body, re.M)
        if name:
            out.append((name.group(1), body))
    return out


def patched_runtime_features(version: str) -> str | None:
    """The json5 as the series leaves it, rebuilt from pristine + patches."""
    pristine = REPO / "browser" / ".pristine" / version / RUNTIME_FEATURES_JSON5
    if not pristine.is_file():
        return None
    touching = [
        p
        for p in sorted(PATCHES.glob("*.patch"))
        if RUNTIME_FEATURES_JSON5 in p.read_text()
    ]
    if not touching:
        return pristine.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / RUNTIME_FEATURES_JSON5
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(pristine.read_text())
        for patch in touching:
            done = subprocess.run(
                ["git", "apply", "-p1", f"--include={RUNTIME_FEATURES_JSON5}", str(patch)],
                cwd=tmp,
                capture_output=True,
                text=True,
            )
            if done.returncode != 0:
                # check-dead-patches.py owns "does it apply"; do not double-report.
                return None
        return dest.read_text()


def upstream_hits(src: pathlib.Path, pattern: str) -> list[str]:
    """Lines under FEATURE_SCAN_ROOTS matching pattern, excluding build output."""
    roots = [str(src / r) for r in FEATURE_SCAN_ROOTS if (src / r).is_dir()]
    if not roots:
        return []
    done = subprocess.run(
        ["grep", "-rnE", "--include=*.cc", "--include=*.h", pattern, *roots],
        capture_output=True,
        text=True,
    )
    return [
        line[len(str(src)) + 1 :] if line.startswith(str(src) + "/") else line
        for line in done.stdout.splitlines()
        if "/out/" not in line
    ]


def check_runtime_features(
    version: str, src: pathlib.Path | None
) -> tuple[list[str], int, int]:
    problems: list[str] = []
    checked = skipped = 0

    patched = patched_runtime_features(version)
    non_public = []
    if patched is not None:
        pristine_text = (
            REPO / "browser" / ".pristine" / version / RUNTIME_FEATURES_JSON5
        ).read_text()
        was_public = {
            name
            for name, body in json5_entries(pristine_text)
            if re.search(r"^\s*public:\s*true", body, re.M)
        }
        non_public = [
            name
            for name, body in json5_entries(patched)
            if name in was_public
            and re.search(r"^\s*public:\s*false", body, re.M)
        ]

    # (a) a feature the series demotes out of the public API, that upstream sets
    for name in non_public:
        if src is None:
            skipped += 1
            problems.append(
                f"{RUNTIME_FEATURES_JSON5}: the series sets `public: false` on "
                f"{name}, deleting WebRuntimeFeatures::Enable{name}() -- pass "
                f"--src to prove no upstream caller remains"
            )
            continue
        checked += 1
        hits = upstream_hits(src, rf"\bEnable{name}\b")
        for hit in hits[:5]:
            problems.append(
                f"{RUNTIME_FEATURES_JSON5}: `public: false` on {name} removes "
                f"WebRuntimeFeatures::Enable{name}(), still called at {hit.split(':')[0]}"
                f":{hit.split(':')[1]}"
            )

    # (b) a feature we rely on being write-only that has grown a reader
    for name, why in WRITE_ONLY_FEATURES:
        if src is None:
            skipped += 1
            continue
        checked += 1
        for hit in upstream_hits(src, rf"\b{name}Enabled\s*\(")[:5]:
            problems.append(
                f"{name} is assumed write-only but is READ at "
                f"{hit.split(':')[0]}:{hit.split(':')[1]} -- {why}"
            )

    return problems, checked, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--offline",
        action="store_true",
        help="only use the local mirror; do not query googlesource",
    )
    ap.add_argument(
        "--src",
        help="a real chromium/src checkout — the authoritative answer for "
        "include resolution, since it contains the DEPS-managed repos too",
    )
    args = ap.parse_args()
    src = pathlib.Path(args.src) if args.src else None
    if src is not None and not src.is_dir():
        raise SystemExit(f"--src is not a directory: {src}")

    version = pinned_version()
    print(f"== patch API check vs Chromium {version}\n")

    banned = check_banned()
    print(f"-- banned spellings ({len(BANNED)} rules)")
    if banned:
        for problem in banned:
            print(f"   !! {problem}")
    else:
        print("   ok")

    print()
    try:
        includes, checked, skipped = check_includes(version, args.offline, src)
    except Unverifiable as e:
        print(f"!! googlesource did not answer, so the includes are unverified: {e}")
        return 1
    where = f"against {src}" if src else "against the mirror + googlesource"
    print(f"-- added includes {where} ({checked} verified, {skipped} not checkable)")
    if includes:
        for problem in includes:
            print(f"   !! {problem}")
    else:
        print("   ok")

    print()
    features, fchecked, fskipped = check_runtime_features(version, src)
    print(
        f"-- runtime feature gating ({fchecked} verified, {fskipped} not checkable)"
    )
    if features:
        for problem in features:
            print(f"   !! {problem}")
    else:
        print("   ok")

    total = len(banned) + len(includes) + len(features)
    print()
    if total:
        print(
            f"!! {total} problem(s) — these WILL fail the compile, or "
            "silently unspoof a surface."
        )
        return 1
    print("All good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
