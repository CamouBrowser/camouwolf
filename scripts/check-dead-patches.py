#!/usr/bin/env python3
"""Test every hunk in the Camouwolf series against a pristine Chromium tree.

Run this after EVERY Chromium bump, before attempting a build. A bump moves code
around; a patch whose context drifted does one of three things, and only the
first is loud on its own:

  DEAD      — no longer applies. The build fails, you notice.
  CORRUPT   — applies forward AND reverse against pristine. That means the hunk
              is a no-op or matches in more than one place; GNU patch may land it
              somewhere harmless-looking and the surface silently goes
              unspoofed. This is the one that ships a broken stealth build that
              passes a smoke test.
  MISSFILE  — the file was renamed or deleted upstream. The surface may have
              moved to a new file that now needs its own patch.

Usage:
    python3 scripts/check-dead-patches.py                 # uses the mirror
    python3 scripts/check-dead-patches.py --src /path/to/chromium/src

Exit code is non-zero if anything is not OK, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PATCHES = REPO / "browser" / "patches"

FILE_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.M)
# A create-file diff: `diff --git a/X b/X` followed by `new file mode`.
CREATED_RE = re.compile(r"^diff --git a/(\S+) b/\S+\nnew file mode ", re.M)


def pinned() -> tuple[str, str]:
    version = release = ""
    for line in (REPO / "browser" / "upstream.sh").read_text().splitlines():
        if line.startswith("version="):
            version = line.split("=", 1)[1].strip()
        elif line.startswith("release="):
            release = line.split("=", 1)[1].strip()
    return version, release


def series() -> list[str]:
    out = []
    for line in (PATCHES / "series").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def git(src: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=src, capture_output=True, text=True
    )


def classify(src: pathlib.Path, patch: pathlib.Path) -> tuple[str, str]:
    """Return (verdict, detail) for one patch against a pristine tree."""
    text = patch.read_text()
    touched = FILE_RE.findall(text)
    # Files the patch CREATES are expected to be absent from a pristine tree —
    # that is what `new file mode` means. Only a file the patch expects to
    # modify is a MISSFILE when it is not there.
    created = set(CREATED_RE.findall(text))
    missing = [f for f in touched if f not in created and not (src / f).exists()]
    if missing:
        return "MISSFILE", ", ".join(missing)
    # Conversely, a created file that DOES exist upstream means the bump landed
    # a file of that name; applying would clobber it.
    clobbers = [f for f in created if (src / f).exists()]
    if clobbers:
        return "MISSFILE", "upstream now ships " + ", ".join(clobbers)

    fwd = git(src, "apply", "--check", "-p1", str(patch))
    rev = git(src, "apply", "--check", "--reverse", "-p1", str(patch))

    if fwd.returncode == 0 and rev.returncode == 0:
        # Applying in both directions against the SAME pristine tree means the
        # hunks are not anchored to a unique location.
        return "CORRUPT", "applies forward AND reverse against pristine"
    if fwd.returncode == 0:
        return "OK", f"{len(touched)} file(s)"
    if rev.returncode == 0:
        return "APPLIED", "already applied to this tree — is it really pristine?"

    first = (fwd.stderr.strip().splitlines() or ["unknown"])[0]
    return "DEAD", first


def check_series(src: pathlib.Path, names: list[str], patches: pathlib.Path = PATCHES) -> list[tuple[str, str, str]]:
    """(name, verdict, detail) for each patch, in series order. Each patch is
    checked against the tree the build would hand it: pristine plus every
    earlier patch, since two patches that each apply alone can collide."""
    out = []
    for name in names:
        patch = patches / name
        if not patch.exists():
            verdict, detail = "MISSING", "not on disk"
        else:
            verdict, detail = classify(src, patch)
        if verdict == "OK":
            git(src, "apply", "-p1", str(patch)).check_returncode()
        out.append((name, verdict, detail))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        help="a pristine chromium/src checkout at the pinned tag "
        "(default: the browser/.pristine mirror, which only holds "
        "the files the series touches)",
    )
    args = ap.parse_args()

    version, release = pinned()
    print(f"== Camouwolf {release} vs Chromium {version}\n")

    if args.src:
        origin = pathlib.Path(args.src)
        if not origin.is_dir():
            print(f"!! not a directory: {origin}", file=sys.stderr)
            return 2
    else:
        origin = REPO / "browser" / ".pristine" / version
        if not origin.is_dir():
            print(
                "!! no mirror — run: bash browser/scripts/fetch-sources.sh",
                file=sys.stderr,
            )
            return 2
    # The series is applied as it is checked, so it runs on a throwaway copy of
    # the files it touches, never on the checkout: ~40k lines, not 100 GB.
    tmp = tempfile.mkdtemp(prefix="camouwolf-deadpatch-")
    src = pathlib.Path(tmp) / "src"
    touched = {f for name in series() if (PATCHES / name).exists()
               for f in FILE_RE.findall((PATCHES / name).read_text())}
    for rel in touched:
        if (origin / rel).is_file():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin / rel, src / rel)
    src.mkdir(parents=True, exist_ok=True)
    git(src, "init", "-q")
    git(src, "add", "-A", "-f")
    git(src, "-c", "user.email=k@k", "-c", "user.name=k", "commit", "-qm", "base")

    counts: dict[str, int] = {}
    try:
        for name, verdict, detail in check_series(src, series()):
            counts[verdict] = counts.get(verdict, 0) + 1
            mark = "  " if verdict == "OK" else "!!"
            print(f"{mark} {verdict:<9} {name:<44} {detail}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    bad = sum(v for k, v in counts.items() if k != "OK")
    if bad:
        print(f"\n!! {bad} patch(es) need reconciliation before building.")
        return 1
    print("\nAll patches apply cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
