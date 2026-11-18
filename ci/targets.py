"""The build targets, and which of them CI builds.

One table, read by ci.run_prepare, ci.run_build, ci.run_install and the
workflows' matrices (`python -m ci.targets --matrix`), so "is Windows built?"
has one answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, NamedTuple, Optional

from ._util import read_upstream_sh


class Target(NamedTuple):
    name: str
    archive_suffix: str  # as scripts/release-camouwolf.sh names it
    enabled: bool
    disabled_reason: str  # one line; empty when enabled


TARGETS: Dict[str, Target] = {
    "linux": Target("linux", "lin.x86_64", True, ""),
    "windows": Target(
        "windows", "win.x86_64", False,
        "no Windows build of this patch series has been made or tested; the packaging path exists, nothing has run it",
    ),
    "macos": Target(
        "macos", "mac.arm64", False,
        "needs a macOS host with Xcode, and no runner has one",
    ),
}


def get(name: str) -> Target:
    if name not in TARGETS:
        raise SystemExit(f"unknown target {name!r}; known: {', '.join(TARGETS)}")
    return TARGETS[name]


def require_enabled(name: str) -> Target:
    target = get(name)
    if not target.enabled:
        raise SystemExit(f"target {name} is disabled: {target.disabled_reason}")
    return target


def archive_name(target: Target) -> str:
    """The archive scripts/release-camouwolf.sh writes into browser/dist/."""
    up = read_upstream_sh()
    return f"camouwolf-{up['version']}-{up['release']}-{target.archive_suffix}.zip"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", action="store_true", help="the enabled targets, as a workflow matrix")
    args = parser.parse_args(argv)
    if args.matrix:
        print(json.dumps({"include": [
            {"target": t.name, "archive": archive_name(t)} for t in TARGETS.values() if t.enabled
        ]}))
        return 0
    for t in TARGETS.values():
        print(f"{t.name:8} {'enabled' if t.enabled else 'disabled: ' + t.disabled_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
