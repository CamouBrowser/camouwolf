#!/usr/bin/env python3
"""Build-tester: does a built browser carry the identity the launcher gave it?

Launches `profile_count` identities per OS through the launcher, on a private
X display, and for each compares what the launcher claimed with what a page, an
iframe, a worker and the request headers report (tests/build-tester/). Every
check is recorded as `<profile>::<category>::<check>`.

Policy is ci/build-tester.yml: a failed check in a required category fails the
gate; the reported categories are in the result and do not gate, each for the
reason the file gives. A category the driver emits that the policy names in
neither list fails the run, so a new category cannot arrive ungated by default.

Run:
    python3 -m ci.run_build_tester --binary path/to/camouwolf
    python3 -m ci.run_build_tester --binary path/to/camouwolf --profiles 1 --os linux
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import results
from ._pytest import require_binary
from ._util import REPO_ROOT, RESULTS_DIR, read_json, run, work_dir
from .browser import binary_version, private_display

POLICY = Path(__file__).with_name("build-tester.yml")
DRIVER = REPO_ROOT / "tests" / "build-tester" / "run_tests.py"


def load_policy(path: Path = POLICY) -> Dict[str, Any]:
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, reason in (policy.get("reported_categories") or {}).items():
        if not str(reason or "").strip():
            raise ValueError(f"{path.name}: reported category {name!r} has no reason")
    overlap = set(policy.get("required_categories") or []) & set(policy.get("reported_categories") or {})
    if overlap:
        raise ValueError(f"{path.name}: both required and reported: {sorted(overlap)}")
    return policy


def flatten(report: Dict[str, Any]) -> List[Tuple[str, str, str, bool, str]]:
    """(profile, category, check, passed, detail) for every check in a report."""
    rows = []
    for profile in report.get("profiles", []):
        for category, checks in (profile.get("results") or {}).items():
            for name, outcome in checks.items():
                rows.append((profile["name"], category, name, bool(outcome.get("passed")), str(outcome.get("detail", ""))))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("CI_SUITE_PYTHON") or sys.executable),
                        help="interpreter with playwright and the launcher's dependencies")
    parser.add_argument("--profiles", type=int, help="identities per OS (default: build-tester.yml)")
    parser.add_argument("--os", dest="os_names", nargs="+", help="OS identities (default: build-tester.yml)")
    parser.add_argument("--headed", action="store_true", help="headed, still on the private display")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)

    result = results.GateResult(gate="build_tester")
    try:
        binary = require_binary(args.binary)
        policy = load_policy()
    except (FileNotFoundError, ValueError) as exc:
        result.note(str(exc))
        result.finish(results.ERROR).save(args.results_dir)
        return 1

    profiles = args.profiles or int(policy["profile_count"])
    os_names = args.os_names or list(policy["os"])
    out = work_dir() / "build-tester-report.json"
    out.unlink(missing_ok=True)
    cmd = [str(args.python), str(DRIVER), "--binary", str(binary), "--out", str(out),
           "--profiles", str(profiles), "--os", *os_names, *(["--headed"] if args.headed else [])]
    with private_display():
        proc = run(cmd, cwd=REPO_ROOT, timeout=args.timeout)
    result.metrics.update(browser_version=binary_version(binary), profiles_per_os=profiles, os=os_names,
                          headless=not args.headed)
    if not out.is_file():
        result.note(f"the driver exited {proc.code} and wrote no report:\n{proc.combined()[-1500:]}")
        result.finish(results.ERROR).save(args.results_dir)
        return 1

    report = read_json(out)
    rows = flatten(report)
    expected = profiles * len(os_names)
    required = set(policy["required_categories"])
    reported = dict(policy.get("reported_categories") or {})
    problems: List[str] = []
    if len(report.get("profiles", [])) != expected:
        problems.append(f"{len(report.get('profiles', []))} profiles ran, {expected} were asked for")
    unknown = sorted({category for _, category, _, _, _ in rows} - required - set(reported))
    if unknown:
        problems.append(f"categories ci/build-tester.yml lists as neither required nor reported: {', '.join(unknown)}")

    failed_required: Dict[str, List[str]] = {}
    failed_reported: Dict[str, int] = {}
    for profile, category, name, passed, detail in rows:
        tid = f"{profile}::{category}::{name}"
        if passed:
            result.record(tid, results.PASS)
        elif category in reported:
            # In the result with its outcome visible, not counted against the gate.
            result.record(tid, results.SKIP)
            failed_reported[category] = failed_reported.get(category, 0) + 1
        else:
            result.record(tid, results.FAIL)
            failed_required.setdefault(f"{category}::{name}", []).append(f"{profile}: {detail}")

    for check_id, where in sorted(failed_required.items()):
        result.note(f"FAIL {check_id} in {len(where)} profile(s). {where[0][:400]}")
    for category, count in sorted(failed_reported.items()):
        result.note(f"reported, not gating: {count} failed check(s) in {category} ({' '.join(str(reported[category]).split())})")
    for problem in problems:
        result.note(problem)
    result.metrics.update(failed_checks=sorted(failed_required), reported_failures=failed_reported)
    result.artifacts.append(out.name)

    tally = result.tally()
    result.note(f"{tally.get('pass', 0)} passed, {tally.get('fail', 0)} failed, "
                f"{tally.get('skip', 0)} reported-only failures ({tally.get('total', 0)} checks, "
                f"{len(report.get('profiles', []))} profiles)")
    status = results.PASS if not failed_required and not problems else results.FAIL
    result.finish(status).save(args.results_dir)
    return 0 if status == results.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
