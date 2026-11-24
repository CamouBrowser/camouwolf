#!/usr/bin/env python3
"""typescript gate: the npm package's own checks and test suite.

Type check, lint, build, the packed tarball, and the vitest suite. No browser
binary. Includes the golden tests that hold the TypeScript launcher to the
Python launcher's output for the same request, so a change to python/ that is
not mirrored in typescript/ fails here, on a hosted runner, before any build.

The suite's display tests start X servers of their own; the whole run sits on
a private X display so nothing in it can reach a desktop.

Run:
    python3 -m ci.run_typescript
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

from . import results as evidence
from ._util import EVIDENCE_DIR, REPO_ROOT, run, work_dir
from .browser import private_display

TYPESCRIPT = REPO_ROOT / "typescript"


def parse_vitest_junit(path: Path) -> Dict[str, str]:
    """vitest junit XML -> {"tests/file.test.ts::suite > test": outcome}.

    vitest puts the test file in `classname` and the describe path in `name`,
    which already make a stable identity; pytest's dotted-module trimming in
    ci/_pytest.py would mangle a `.test.ts` path.
    """
    if not path.exists():
        return {}
    outcomes: Dict[str, str] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        tid = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("error") is not None:
            outcome = evidence.ERROR
        elif case.find("failure") is not None:
            outcome = evidence.FAIL
        elif case.find("skipped") is not None:
            outcome = evidence.SKIP
        else:
            outcome = evidence.PASS
        if outcomes.get(tid) == evidence.PASS:
            continue
        outcomes[tid] = outcome
    return outcomes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    parser.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="interpreter with the Python launcher's dependencies: the "
                             "golden fixtures are recorded from it")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    gate = "typescript"
    result = evidence.GateResult(gate=gate)
    if not (TYPESCRIPT / "package.json").is_file():
        result.note("typescript/package.json does not exist")
        result.finish(evidence.ERROR).save(args.evidence_dir)
        return 1

    env = dict(os.environ)
    # `pnpm golden` and the golden tests record from this interpreter.
    env["CAMOUWOLF_PYTHON"] = str(args.python.absolute())

    # Every test input must be something a checkout gets. A git-ignored file
    # under src/, tests/ or scripts/ exists on the machine that made it and
    # nowhere else, so the suite passes there and fails in CI .
    ignored = run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory",
         "--", "typescript/src", "typescript/tests", "typescript/scripts"],
        cwd=REPO_ROOT,
    )
    # A .gitignore inside typescript/tests/fixtures/ may ignore what the suite
    # generates there on purpose; anything another rule hides is stray.
    candidates = [p for p in ignored.stdout.split() if "__pycache__" not in p]
    rules = run(["git", "check-ignore", "--verbose", "--no-index", *candidates], cwd=REPO_ROOT) \
        if candidates else None
    generated = {
        line.split("\t", 1)[1] for line in (rules.stdout.splitlines() if rules else [])
        if line.startswith("typescript/tests/fixtures/.gitignore:")
    }
    stray = [p for p in candidates if p not in generated]
    for path in stray:
        result.note(f"git-ignored test input: {path}")
    result.record("no test input is git-ignored", evidence.FAIL if stray else evidence.PASS)

    install = run(["pnpm", "install", "--frozen-lockfile"], cwd=TYPESCRIPT, env=env,
                  timeout=600, tee=True, capture=False)
    if not install.ok:
        result.note(f"pnpm install exited {install.code}")
        result.finish(evidence.ERROR).save(args.evidence_dir)
        return 1

    # Static checks are recorded as tests of their own, so the summary names
    # which one failed instead of reporting a bare non-zero exit.
    for script in ("typecheck", "check"):
        proc = run(["pnpm", script], cwd=TYPESCRIPT, env=env, timeout=600, tee=True, capture=False)
        result.record(f"pnpm {script}", evidence.PASS if proc.ok else evidence.FAIL)

    # The tarball a user would install: builds, ships every data file, installs
    # and imports in an empty project, and its CLI starts. release.yml runs the
    # same check before publishing.
    build = run(["pnpm", "build"], cwd=TYPESCRIPT, env=env, timeout=600, tee=True, capture=False)
    result.record("pnpm build", evidence.PASS if build.ok else evidence.FAIL)
    pack = build.ok and run(["node", "scripts/check-pack.mjs"], cwd=TYPESCRIPT, env=env,
                            timeout=900, tee=True, capture=False).ok
    result.record("npm package (scripts/check-pack.mjs)", evidence.PASS if pack else evidence.FAIL)

    junit = work_dir() / f"junit-{gate}.xml"
    # A run that dies before writing junit must not leave the last run's.
    junit.unlink(missing_ok=True)
    with private_display():
        proc = run(
            ["pnpm", "exec", "vitest", "run",
             "--reporter=default", "--reporter=junit", f"--outputFile.junit={junit}"],
            cwd=TYPESCRIPT, env={**env, "DISPLAY": os.environ["DISPLAY"], "WAYLAND_DISPLAY": ""},
            timeout=args.timeout, tee=True, capture=False,
        )
    outcomes = parse_vitest_junit(junit)
    if not outcomes:
        result.note(f"vitest exited {proc.code} with no junit output; the suite did not run")
        result.finish(evidence.ERROR).save(args.evidence_dir)
        return 1
    for tid, outcome in outcomes.items():
        result.record(tid, outcome)

    tally = result.tally()
    result.artifacts.append(junit.name)
    result.metrics["exit_code"] = proc.code
    result.note(
        f"{tally.get('pass', 0)} passed, {tally.get('fail', 0)} failed, "
        f"{tally.get('error', 0)} errored, {tally.get('skip', 0)} skipped "
        f"({tally.get('total', 0)} collected)"
    )
    failing = tally.get("fail", 0) + tally.get("error", 0)
    status = evidence.PASS if failing == 0 else evidence.FAIL
    result.finish(status).save(args.evidence_dir)
    return 0 if status == evidence.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
