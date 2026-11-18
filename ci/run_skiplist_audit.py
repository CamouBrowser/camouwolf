#!/usr/bin/env python3
"""Every skiplist entry must still be failing.

`ci/skiplist.yml` deselects tests Camouwolf does not pass by design. The failure
mode of such a list is not that it grows -- it is that it stops being true. A
test gets fixed, or the behaviour it asserted changes, and the entry sits there
skipping a test that would now pass. Nothing notices, because a skipped test
looks exactly like a skipped test either way.

So: run the skipped tests with the skiplist disabled, and fail if any of them
passes. A written reason is an assertion about the browser, and this is the
thing that checks it.

Each target runs in its own pytest process, without upstream's reruns. Some
upstream modules share one browser across their tests, and browser state left
by one test (an HTTPS-First fallback exemption, for one) can decide whether the
next passes. One process per target gives each entry a fresh browser, so its
outcome does not depend on which entries ran before it.

Cheap, because a correct skiplist is short -- it runs only what the list names.

Run:
    python3 -m ci.run_skiplist_audit --binary path/to/camouwolf
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import results
from ._pytest import parse_junit, require_binary, run_pytest
from ._util import REPO_ROOT, RESULTS_DIR, WORK_DIR, log
from .browser import binary_version, private_display
from .pw_camouwolf_plugin import load_skiplist
from .run_playwright import BASE_ARGS, suite_env
from .suite import prepare
from .versions import resolve


def targets(entries: List[dict]) -> Tuple[List[str], List[str]]:
    """(pytest targets, entries we cannot turn into one).

    `module` and `test` name something pytest can select. `pattern` is a
    substring match over node ids with no path in it, so it is reported rather
    than audited -- and saying so is the point, because an unaudited entry that
    looked audited is the bug this module exists to prevent.
    """
    selectable: List[str] = []
    unresolved: List[str] = []
    for entry in entries:
        if "checked_by" in entry:
            continue  # Its outcome varies with machine speed; a patch guard checks its reason.
        if "module" in entry:
            selectable.append(str(entry["module"]).lstrip("./"))
        elif "test" in entry:
            selectable.append(str(entry["test"]).lstrip("./"))
        else:
            unresolved.append(str(entry.get("pattern", entry)))
    return selectable, unresolved


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--browser-version", help="passed through to ci.versions")
    parser.add_argument("--playwright-tag", help="pin the suite instead of resolving one")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--python", type=Path, help="interpreter to run the suite with (see ci.suite)")
    parser.add_argument("--os", dest="os_name", default="linux", choices=["linux", "windows", "macos"])
    parser.add_argument("--timeout", type=int, default=300, help="seconds one entry may take before it is killed")
    args = parser.parse_args(argv)

    result = results.GateResult(gate="skiplist_audit")

    entries = load_skiplist(REPO_ROOT / "ci" / "skiplist.yml")
    if not entries:
        result.note("the skiplist is empty; nothing to audit")
        result.finish(results.PASS).save(args.results_dir)
        return 0

    selectable, unresolved = targets(entries)
    for pattern in unresolved:
        result.note(f"not audited (pattern entries name no file): {pattern!r}")

    try:
        binary = require_binary(args.binary)
    except FileNotFoundError as exc:
        result.note(str(exc))
        result.finish(results.ERROR).save(args.results_dir)
        return 1

    versions = resolve(
        browser_version=args.browser_version or binary_version(binary),
        playwright_tag=args.playwright_tag,
    )
    manifest = prepare(versions["playwright_tag"], python=args.python)
    cwd = Path(manifest["checkout"])

    # An empty skiplist, so the plugin still routes every launch through the
    # launcher while deselecting nothing. A test that hits the per-test timeout
    # has failed, which is what several entries are there for.
    outcomes: Dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp, private_display():
        empty = Path(tmp) / "skiplist.yml"
        empty.write_text("schema: 1\nskip: []\n", encoding="utf-8")
        junit = WORK_DIR / "junit-skiplist-audit.xml"
        log(f"auditing {len(selectable)} skiplist target(s) with the skiplist disabled")
        for target in selectable:
            run = run_pytest(
                cwd=cwd,
                python=Path(manifest["python"]),
                args=[*BASE_ARGS, "-p", "no:cacheprovider", target],
                junit=junit,
                env={
                    **suite_env(binary, args.os_name),
                    "CI_SKIPLIST": str(empty),
                    # Upstream's conftest reruns failures under $CI, in the same browser.
                    "CI": "",
                },
                timeout=args.timeout,
            )
            found = parse_junit(junit)
            if not found and run.code == 124:
                # The test never finished (several entries are tests that hang),
                # and a test that never finishes has not passed.
                result.note(f"{target} did not finish within {args.timeout}s and was killed")
                found = {target: results.FAIL}
            if not found:
                result.note(
                    f"auditing {target} produced no junit results, so it was not verified. "
                    "Treating that as a failure: an audit that did not run is not an audit "
                    "that passed."
                )
                result.finish(results.ERROR).save(args.results_dir)
                return 1
            outcomes.update(found)

    stale = sorted(t for t, o in outcomes.items() if o == results.PASS)
    for test in stale:
        result.record(test, results.FAIL)
    for test, outcome in outcomes.items():
        if outcome != results.PASS:
            result.record(test, results.PASS)

    if stale:
        result.note(
            f"{len(stale)} skiplisted test(s) now PASS, so their entry in ci/skiplist.yml "
            "is no longer true. Remove the entry (or narrow it to what still fails) rather "
            "than leaving a passing test skipped: " + ", ".join(stale[:12])
            + (" ..." if len(stale) > 12 else "")
        )
        result.finish(results.FAIL).save(args.results_dir)
        return 1

    result.note(
        f"{len(outcomes)} skiplisted test(s) checked; every one still fails, so every "
        "entry still describes something true."
    )
    result.finish(results.PASS).save(args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
