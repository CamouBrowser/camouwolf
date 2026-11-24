#!/usr/bin/env python3
"""Emit a Camouwolf patch from the pristine/work mirror pair.

Patch authoring flow (see docs/PATCHES.md):

    bash browser/scripts/fetch-sources.sh          # mirror the pinned tag
    cp -r browser/.pristine/$version browser/.work # work tree
    $EDITOR browser/.work/<file>                   # make the change
    python3 browser/scripts/mkpatch.py 0002-navigator-spoofing <file> [<file> ...]

The emitted diff uses `a/`+`b/` prefixes and 3 lines of context, so it applies
with `git apply -p1` / `patch -p1` against a real chromium/src checkout at the
pinned tag. Anything that does not apply cleanly there is a REAL conflict, not a
formatting artifact — which is the whole point of authoring against the exact
tag rather than by hand.
"""

from __future__ import annotations

import difflib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
BROWSER = REPO / "browser"


def pinned_version() -> str:
    for line in (BROWSER / "upstream.sh").read_text().splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no version= in browser/upstream.sh")


def unified(rel: str, pristine: pathlib.Path, work: pathlib.Path) -> str:
    old = pristine.read_text().splitlines(keepends=True)
    new = work.read_text().splitlines(keepends=True)
    if old == new:
        return ""
    body = "".join(
        difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3)
    )
    return f"diff --git a/{rel} b/{rel}\n{body}"


def added(rel: str, work: pathlib.Path) -> str:
    """Emit a create-file diff for a file Camouwolf adds that upstream has no copy of.

    A file we introduce has no pristine counterpart, so the usual pristine/work
    diff cannot describe it. git apply only CREATES a file when the diff carries
    `new file mode` and reads from /dev/null — pointing `---` at `a/<path>`
    instead makes it fail with "No such file or directory", so both details are
    load-bearing.
    """
    new = work.read_text().splitlines(keepends=True)
    body = "".join(
        difflib.unified_diff([], new, fromfile="/dev/null", tofile=f"b/{rel}", n=3)
    )
    return f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n{body}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    name = argv[1].removesuffix(".patch")
    version = pinned_version()
    pristine_root = BROWSER / ".pristine" / version
    work_root = BROWSER / ".work"

    chunks: list[str] = []
    for rel in argv[2:]:
        rel = rel.removeprefix("./")
        pristine = pristine_root / rel
        work = work_root / rel
        if not work.exists():
            print(f"!! not in work tree: {rel}", file=sys.stderr)
            return 1
        if not pristine.exists():
            # A file Camouwolf adds outright. Upstream has no copy to diff against,
            # so this is a creation rather than a modification. Deliberately NOT
            # an error: the ucrt-math replica and camouwolf_config are new files.
            print(f"   (new file) {rel}", file=sys.stderr)
            chunks.append(added(rel, work))
            continue
        chunk = unified(rel, pristine, work)
        if not chunk:
            print(f"   (unchanged) {rel}", file=sys.stderr)
            continue
        chunks.append(chunk)

    if not chunks:
        print("!! nothing to emit — the work tree matches pristine", file=sys.stderr)
        return 1

    out = BROWSER / "patches" / f"{name}.patch"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(chunks))
    hunks = sum(c.count("\n@@ ") + c.startswith("@@ ") for c in chunks)
    print(f"-> {out.relative_to(REPO)} ({len(chunks)} file(s))")

    # Self-check: the patch must reverse-apply against the work tree, which is
    # the cheapest proof it is internally consistent. A mis-counted @@ header is
    # silently dropped by GNU patch with no .rej — the exact failure mode that
    # cost Camoufox two bad builds — and this catches it at authoring time.
    check = subprocess.run(
        ["git", "apply", "--check", "--reverse", "-p1", str(out)],
        cwd=work_root,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print(f"!! self-check FAILED: {check.stderr.strip()}", file=sys.stderr)
        return 1
    print("   self-check ok (reverse-applies against .work)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
