#!/usr/bin/env python3
"""Patch guards: what can be proved about the patch series without a browser.

A Chromium build is hours; these are seconds. Each guard is a script that exits
0 or 1 and catches a failure that is otherwise found at the end of a build, or
not found at all:

  series-complete     browser/patches/series and the *.patch files on disk name
                      the same set, so no patch is silently left out of a build
  series-applies      every hunk applies forward, and not in reverse, to the
                      pristine files of the pinned Chromium tag
                      (scripts/check-dead-patches.py)
  patch-apis          the code inside the hunks calls things that exist at that
                      tag (scripts/check-patch-apis.py)
  config-keys         the keys C++ reads, the schema, and the keys the launcher
                      writes agree (scripts/check-config-keys.py)
  branding            every committed icon is what the canonical SVG renders to
                      (browser/scripts/generate-branding.py --check)
  bundle-fontconfig   the bundled fontconfig personas parse and resolve
                      (scripts/check-bundle-fontconfig.py)

`series-applies` and `patch-apis` read the mirror of the files the series
touches, `browser/.pristine/<version>/`. It is fetched when missing
(`browser/scripts/fetch-sources.sh`: about 90 files, a few MB).

Policy allows zero failures. Whether the patches *work* in a built browser is
ci.run_build_tester's job.

Run:
    python3 -m ci.run_patch_guards
    python3 -m ci.run_patch_guards --only config-keys branding
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import results as evidence
from ._util import BROWSER_ROOT, EVIDENCE_DIR, REPO_ROOT, Result, read_upstream_sh, run

SCRIPTS = REPO_ROOT / "scripts"
PATCHES = BROWSER_ROOT / "patches"


def mirror() -> Path:
    return BROWSER_ROOT / ".pristine" / read_upstream_sh()["version"]


def series_complete() -> Result:
    """`series` and the patches on disk name the same set."""
    listed = [
        line.strip() for line in (PATCHES / "series").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    on_disk = sorted(p.name for p in PATCHES.glob("*.patch"))
    problems = [f"in series, not on disk: {n}" for n in listed if n not in on_disk]
    problems += [f"on disk, not in series: {n}" for n in on_disk if n not in listed]
    problems += [f"listed twice: {n}" for n in sorted({n for n in listed if listed.count(n) > 1})]
    out = f"{len(listed)} patches in series, {len(on_disk)} on disk"
    return Result(1 if problems else 0, out, "\n".join(problems))


def _script(*cmd: str, timeout: int = 900) -> Callable[[], Result]:
    return lambda: run([sys.executable, *cmd], cwd=REPO_ROOT, timeout=timeout)


def ensure_mirror(fetch: bool) -> Optional[str]:
    """Why the mirror is unusable, or None once it is there."""
    path = mirror()
    if path.is_dir() and any(path.iterdir()):
        return None
    if not fetch:
        return f"no mirror at {path} (run browser/scripts/fetch-sources.sh, or drop --no-fetch)"
    proc = run(["bash", str(BROWSER_ROOT / "scripts" / "fetch-sources.sh")], cwd=REPO_ROOT, timeout=1800)
    if not proc.ok:
        return f"browser/scripts/fetch-sources.sh exited {proc.code}: {proc.combined()[-600:]}"
    return None


def guards(offline: bool) -> Dict[str, Tuple[bool, Callable[[], Result]]]:
    """name -> (needs the mirror, runner). Ordered cheapest first."""
    apis = ["scripts/check-patch-apis.py", *(["--offline"] if offline else [])]
    return {
        "series-complete": (False, series_complete),
        "config-keys": (False, _script("scripts/check-config-keys.py")),
        "bundle-fontconfig": (False, _script("scripts/check-bundle-fontconfig.py")),
        "branding": (False, _script("browser/scripts/generate-branding.py", "--check")),
        "series-applies": (True, _script("scripts/check-dead-patches.py")),
        "patch-apis": (True, _script(*apis)),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="run only these guards")
    parser.add_argument("--no-fetch", action="store_true", help="fail rather than fetch a missing mirror")
    parser.add_argument("--offline", action="store_true",
                        help="check-patch-apis.py: do not ask googlesource about headers the mirror lacks")
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    args = parser.parse_args(argv)

    result = evidence.GateResult(gate="patch_guards")
    table = guards(args.offline)
    unknown = sorted(set(args.only or []) - set(table))
    if unknown:
        result.note(f"no such guard: {', '.join(unknown)}. Known: {', '.join(table)}")
        result.finish(evidence.ERROR).save(args.evidence_dir)
        return 1
    selected = [name for name in table if not args.only or name in args.only]

    mirror_problem: Optional[str] = None
    if any(table[name][0] for name in selected):
        mirror_problem = ensure_mirror(not args.no_fetch)

    up = read_upstream_sh()
    result.metrics.update(version=up.get("version"), release=up.get("release"))
    for name in selected:
        needs_mirror, runner = table[name]
        if needs_mirror and mirror_problem:
            # Not a skip: a guard that could not run has not passed.
            result.record(name, evidence.ERROR)
            result.note(f"{name}: {mirror_problem}")
            continue
        proc = runner()
        result.record(name, evidence.PASS if proc.ok else evidence.FAIL)
        if not proc.ok:
            tail = "\n".join(proc.combined().strip().splitlines()[-25:])
            result.note(f"{name} failed (exit {proc.code}):\n{tail}")

    tally = result.tally()
    failing = tally.get("fail", 0) + tally.get("error", 0)
    result.note(f"{tally.get('pass', 0)} of {tally.get('total', 0)} guards passed")
    status = evidence.PASS if failing == 0 else evidence.FAIL
    result.finish(status).save(args.evidence_dir)
    return 0 if status == evidence.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
