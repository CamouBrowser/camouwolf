#!/usr/bin/env python3
"""Prepare the source tree: sync Chromium at the pinned tag and apply the series.

`browser/scripts/build.sh` with CAMOUWOLF_STAGES="sync patch". Split from the
build so a patch that does not apply fails in minutes, with its own result,
rather than as the first line of a build log.

The sync is a network operation against googlesource, so a failure whose output
looks like the network (a reset, a 5xx, a timeout) is retried with a backoff. A
patch that does not apply is not: retrying it would only take longer to say the
same thing.

Run (on a build machine: this downloads a Chromium checkout):
    python3 -m ci.run_prepare
    python3 -m ci.run_prepare --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import results
from ._util import BROWSER_ROOT, REPO_ROOT, RESULTS_DIR, log, read_upstream_sh, run
from .targets import require_enabled

BUILD_SH = BROWSER_ROOT / "scripts" / "build.sh"

_TRANSIENT = re.compile(
    r"connection reset|connection timed out|could not resolve host|temporary failure|"
    r"HTTP (?:Error )?(?:429|5\d\d)|returned error: (?:429|5\d\d)|early EOF|RPC failed|"
    r"remote end hung up|TLS connection was non-properly terminated|Operation timed out",
    re.I,
)


def is_transient(output: str) -> bool:
    return bool(_TRANSIENT.search(output))


def build_env(stages: str, jobs: Optional[int] = None) -> Dict[str, str]:
    env = {"CAMOUWOLF_STAGES": stages}
    if jobs:
        env["CAMOUWOLF_NINJA_JOBS"] = str(jobs)
    return env


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="linux")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--backoff", type=int, default=60, help="seconds before the first retry")
    parser.add_argument("--timeout", type=int, default=6 * 3600)
    parser.add_argument("--dry-run", action="store_true", help="print what would run")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    require_enabled(args.target)
    env = build_env("sync patch")
    cmd = ["bash", str(BUILD_SH)]
    if args.dry_run:
        print(" ".join(f"{k}={v!r}" for k, v in env.items()), " ".join(cmd))
        return 0

    result = results.GateResult(gate="prepare")
    result.metrics.update(read_upstream_sh(), target=args.target)
    code = 1
    for attempt in range(1, args.attempts + 1):
        proc = run(cmd, cwd=REPO_ROOT, env=env, timeout=args.timeout)
        code = proc.code
        sys.stdout.write(proc.combined()[-20000:])
        if proc.ok:
            break
        if not is_transient(proc.combined()) or attempt == args.attempts:
            result.note(f"sync/patch failed (exit {code}), attempt {attempt}:\n"
                        + "\n".join(proc.combined().strip().splitlines()[-25:]))
            break
        wait = args.backoff * attempt
        log(f"sync hit what looks like a network failure; retrying in {wait}s "
            f"({attempt}/{args.attempts})", level="WARN")
        time.sleep(wait)
    result.metrics["attempts"] = attempt
    result.record("sync and patch", results.PASS if code == 0 else results.FAIL)
    result.finish(results.PASS if code == 0 else results.FAIL).save(args.results_dir)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
