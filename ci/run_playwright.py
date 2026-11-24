#!/usr/bin/env python3
"""Run the Playwright suite against a Camouwolf build.

One suite: playwright-python's own tests, fetched fresh at the tag
`ci/versions.py` resolved for this browser, run unmodified for Chromium with
`ci/skiplist.yml` applied, plus the Camouwolf-specific modules `ci/suite.py`
overlays from `tests/playwright/`. Every launch goes through the launcher
(`ci/pw_camouwolf_plugin.py`), on a private X display.

This is the functionality gate -- does a patched Chromium still honour the
automation contract its users hold it to -- and, through the overlay, the
regression check for the behaviours that are ours alone. Shardable.

Each group runs once. Upstream's own reruns are off and nothing is retried: a
test that fails is a failure, and an intermittent one is a non-determinism to
find. Each launch's identity is seeded from the test that makes it, so a test
draws the same identity on every run.

Run:
    python3 -m ci.run_playwright --binary path/to/camouwolf
    python3 -m ci.run_playwright --binary path/to/camouwolf --shard 3/6
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import results
from ._pytest import parse_junit, require_binary, run_pytest
from ._util import BINARY_ENV, REPO_ROOT, RESULTS_DIR, WORK_DIR, log
from .browser import binary_version, private_display
from .suite import prepare
from .versions import resolve

PLUGIN = "ci.pw_camouwolf_plugin"


# The suite is named path by path rather than as `tests/`, so anything left out
# is visible.
class Group(NamedTuple):
    """A set of paths that share one pytest process, and whether it shards."""

    targets: Tuple[str, ...]
    sharded: bool


# Each group gets its own pytest process: the sync suite's greenlets and the
# async suite's pytest-asyncio loop break each other in one process ("Runner.run()
# cannot be called from a running event loop"). tests/common/ and
# test_reference_count_async.py start their own Playwright, so they cannot share
# a process with session fixtures holding a loop.
GROUPS: Tuple[Group, ...] = (
    Group(("tests/async/",), sharded=True),
    # The sync API is a greenlet wrapper over the same CDP traffic, so much of
    # this duplicates tests/async/ at the protocol level. It is here because the
    # launcher ships a sync API that users drive, and the wrapper has its own
    # timeout and reentrancy behaviour the async tests cannot reach.
    Group(("tests/sync/",), sharded=True),
    # A handful of tests. Not sharded: splitting them would hand some shard an
    # empty selection, which pytest exits 5 for.
    Group(("tests/common/", "tests/test_reference_count_async.py"), sharded=False),
)

TARGETS: Tuple[str, ...] = tuple(t for g in GROUPS for t in g.targets)

# Upstream's conftest sets `reruns = 3` under $CI, which would pass a test that
# fails twice in three.
_NO_UPSTREAM_RERUNS = {"CI": ""}

# Left out on purpose, with the reason, so "not run" is never merely implied.
EXCLUDED = {
    "tests/test_installation.py": (
        "pip-installs playwright into a scratch environment to check packaging. "
        "That exercises Playwright's own release process, not this browser."
    ),
}


def unclaimed(checkout: Path) -> List[str]:
    """Test paths upstream ships that TARGETS neither runs nor EXCLUDED names.

    Upstream is free to add a directory, and the failure mode is silence: the
    suite quietly gets narrower and the total still looks healthy. A new subtree
    fails the run until somebody decides about it.
    """
    claimed = {t.rstrip("/") for t in TARGETS} | set(EXCLUDED)
    root = checkout / "tests"
    missed: List[str] = []
    for child in sorted(root.iterdir()):
        rel = f"tests/{child.name}"
        if rel in claimed:
            continue
        if child.is_dir():
            # Only directories that actually hold tests; assets/ and golden-*/
            # are fixtures.
            if any(child.glob("test_*.py")):
                missed.append(rel + "/")
        elif child.name.startswith("test_") and child.suffix == ".py":
            missed.append(rel)
    return missed


def suite_env(binary: Path, os_name: str) -> Dict[str, str]:
    """What a pytest process in the checkout needs to load the plugin."""
    return {
        BINARY_ENV: str(binary),
        "CI_IDENTITY_OS": os_name,
        # The plugin and the launcher are imported from this repository, so a
        # local edit to either takes effect without re-preparing the suite.
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(REPO_ROOT), str(REPO_ROOT / "python" / "src"),
                          os.environ.get("PYTHONPATH", "")])
        ),
        "CI_SKIPLIST": str(REPO_ROOT / "ci" / "skiplist.yml"),
        # Playwright refuses nothing here, but must never fetch a browser.
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
    }


# `--browser-channel chromium` is what tells upstream's conftest this is a full
# Chromium in headless mode rather than chrome-headless-shell (its
# `is_headless_shell` fixture), which is what a Camouwolf build is.
#
# `--timeout` is per test (pytest-timeout, which upstream's requirements
# install). Upstream sets none, so one test waiting on a dialog that never
# comes would otherwise hold its whole group until the group timeout.
BASE_ARGS: Tuple[str, ...] = (
    "-p", PLUGIN, "--browser", "chromium", "--browser-channel", "chromium", "--timeout", "120",
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--browser-version", help="the Chromium the binary was built from, if "
                        "not upstream.sh's; defaults to the version.json beside the binary")
    parser.add_argument("--playwright-tag", help="pin the suite instead of resolving one")
    parser.add_argument("--python", type=Path, help="interpreter to run the suite with (see ci.suite)")
    parser.add_argument("--os", dest="os_name", default="linux", choices=["linux", "windows", "macos"],
                        help="the identity every launch draws for")
    parser.add_argument("--shard", help="e.g. 3/6")
    parser.add_argument("--only", nargs="*", help="run these paths or node ids instead of the groups "
                        "(local diagnosis; the result is named playwright_only)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--name", help="result file name; defaults to playwright[-shard]")
    # Per pytest invocation, and shorter than the job's timeout-minutes so it can
    # fire before the runner is killed and the uploads with it.
    parser.add_argument("--group-timeout", type=int, default=3600)
    args = parser.parse_args(argv)

    suffix = f"-{args.shard.replace('/', 'of')}" if args.shard else ""
    name = args.name or ("playwright_only" if args.only else f"playwright{suffix}")
    result = results.GateResult(gate=name)

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
    tag = versions["playwright_tag"]
    result.metrics.update(
        playwright_tag=tag,
        playwright_chromium=versions["playwright_chromium"],
        browser_version=versions["browser_version"],
        identity_os=args.os_name,
    )

    manifest = prepare(tag, python=args.python)
    cwd = Path(manifest["checkout"])
    python = Path(manifest["python"])
    result.metrics["camouwolf_tests"] = len(manifest.get("camouwolf_tests", []))

    missed = unclaimed(cwd)
    if missed:
        result.note(
            f"{tag} ships test paths this runner neither runs nor excludes: "
            + ", ".join(missed)
            + ". Add them to TARGETS, or to EXCLUDED with a reason. Refusing to report a "
            "pass over a suite that quietly got narrower."
        )
        result.finish(results.ERROR).save(args.results_dir)
        return 1

    env = suite_env(binary, args.os_name)
    if args.shard:
        result.metrics["shard"] = args.shard
    groups = (Group(tuple(args.only), sharded=False),) if args.only else GROUPS

    first_shard = not args.shard or args.shard.split("/")[0] == "1"
    outcomes: Dict[str, str] = {}
    ran: List[Group] = []
    junits: List[str] = []
    last_code = 0

    with private_display():
        for index, group in enumerate(groups):
            if not group.sharded and not first_shard:
                continue
            group_env = {**env, **_NO_UPSTREAM_RERUNS}
            if args.shard and group.sharded:
                group_env["CI_SHARD"] = args.shard

            common = [*BASE_ARGS, "-p", "no:cacheprovider"]
            targets = ", ".join(group.targets)

            log(f"group {index + 1}/{len(groups)}: {targets}")
            group_junit = WORK_DIR / f"junit-{name}-{index}.xml"
            proc = run_pytest(
                cwd=cwd,
                python=python,
                args=[*common, *group.targets],
                junit=group_junit,
                env=group_env,
                timeout=args.group_timeout,
            )
            last_code = proc.code
            part = parse_junit(group_junit)
            if not part:
                result.note(
                    f"{targets} exited {proc.code} and produced no junit "
                    "results. That group did not run; it is a failure, not an empty pass."
                )
                result.finish(results.ERROR).save(args.results_dir)
                return 1
            junits.append(group_junit.name)
            outcomes.update(part)
            ran.append(group)

    result.metrics["groups"] = len(ran)
    for tid, outcome in outcomes.items():
        result.record(tid, outcome)

    tally = result.tally()
    result.artifacts.extend(junits)
    result.metrics["exit_code"] = last_code
    result.note(
        f"{tally.get('pass', 0)} passed, {tally.get('fail', 0)} failed, "
        f"{tally.get('error', 0)} errored, {tally.get('skip', 0)} skipped "
        f"({tally.get('total', 0)} collected)"
    )

    still_failing = tally.get("fail", 0) + tally.get("error", 0)
    status = results.PASS if still_failing == 0 else results.FAIL
    result.finish(status).save(args.results_dir)
    # Exit non-zero so the step goes red in the UI. ci/summarize.py still owns
    # the run's verdict -- it is the only thing that knows what was required --
    # but a green step hiding a failed suite is how a broken pipeline goes
    # unnoticed for a week.
    return 0 if status == results.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
