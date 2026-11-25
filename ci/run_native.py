#!/usr/bin/env python3
"""Camouwolf's own suite: settled decisions, leaks, crashes, proxies and launch options.

Everything the Playwright suite cannot ask about. Split in two so the cheap
half gives fast feedback:

  --subset rules     no browser. Asserts the decisions in ci/tribal-rules.yml
                     are still in force; runs in seconds on a hosted runner and
                     fails a pull request before anything is built.

  --subset browser   needs a built binary. Launches browsers through the
                     launcher, closes and kills them, and checks from the
                     process table that nothing was left behind; runs a
                     proxied launch's traffic through a local SOCKS5 proxy;
                     and checks each launch option that changes what the
                     browser does against a launch without it.

Run:
    python3 -m ci.run_native --subset rules
    python3 -m ci.run_native --subset browser --binary path/to/camouwolf
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import results
from ._pytest import parse_junit, require_binary, run_pytest
from ._util import BINARY_ENV, REPO_ROOT, RESULTS_DIR, work_dir
from .browser import private_display

SUITE_DIR = REPO_ROOT / "tests" / "native"

FILES = {
    "rules": ["test_tribal_rules.py"],
    "browser": ["test_no_leaks.py", "test_crash_recovery.py", "test_canvas_readback.py",
                "test_webrtc_policy.py", "test_webrtc_proxied.py", "test_launch_behaviour.py",
                "test_web_bluetooth.py"],
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subset", choices=[*FILES, "all"], default="all")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("CI_SUITE_PYTHON") or sys.executable),
                        help="interpreter with playwright and the launcher's dependencies (browser subset)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--rounds", type=int, default=3, help="launch/close rounds for the leak test")
    parser.add_argument("--browsers", type=int, default=3, help="browsers open at once in each round")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    subsets = list(FILES) if args.subset == "all" else [args.subset]
    name = "native" if args.subset == "all" else f"native_{args.subset}"
    result = results.GateResult(gate=name)
    env: Dict[str, str] = {
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])),
        "CI_NATIVE_ROUNDS": str(args.rounds),
        "CI_NATIVE_BROWSERS": str(args.browsers),
    }
    needs_browser = "browser" in subsets
    if needs_browser:
        try:
            env[BINARY_ENV] = str(require_binary(args.binary))
        except FileNotFoundError as exc:
            result.note(str(exc))
            result.finish(results.ERROR).save(args.results_dir)
            return 1

    files = [f for subset in subsets for f in FILES[subset]]
    junit = work_dir() / f"junit-{name}.xml"
    junit.unlink(missing_ok=True)
    with private_display() if needs_browser else contextlib.nullcontext():
        proc = run_pytest(
            cwd=SUITE_DIR,
            python=args.python,
            args=["-p", "no:cacheprovider", "--timeout", "300", *files],
            junit=junit,
            env=env,
            timeout=args.timeout,
        )
    outcomes = parse_junit(junit)
    if not outcomes:
        result.note(f"pytest exited {proc.code} and produced no junit results; the suite did not run")
        result.finish(results.ERROR).save(args.results_dir)
        return 1
    for tid, outcome in outcomes.items():
        result.record(tid, outcome)
    tally = result.tally()
    result.artifacts.append(junit.name)
    result.note(f"{tally.get('pass', 0)} passed, {tally.get('fail', 0)} failed, "
                f"{tally.get('error', 0)} errored, {tally.get('skip', 0)} skipped")
    failing = tally.get("fail", 0) + tally.get("error", 0)
    status = results.PASS if failing == 0 else results.FAIL
    result.finish(status).save(args.results_dir)
    return 0 if status == results.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
