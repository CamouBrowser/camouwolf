"""The build targets, and which of them CI builds.

One table, read by ci.run_prepare, ci.run_build, tests.yml and
`python -m ci.targets`, so "is Windows built?" has one answer. tests.yml builds
and tests TESTED; release.yml publishes that build and builds the other enabled
targets from `python -m ci.targets --release-matrix`.

A target is an OS and a CPU. Every one is cross-compiled on a Linux host
(ci/tribal-rules.yml: hosted-runners-only); docs/CROSS-COMPILING.md
says what each needs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, NamedTuple, Optional

from ._util import read_upstream_sh

# The launchers' names for each OS (pkgman.py OS_ARCH_MATRIX).
OS_SHORT = {"linux": "lin", "windows": "win", "macos": "mac"}


class Target(NamedTuple):
    os: str
    arch: str
    enabled: bool
    disabled_reason: str  # one line; empty when enabled

    @property
    def name(self) -> str:
        return f"{self.os}-{self.arch}"

    @property
    def archive_suffix(self) -> str:
        """As scripts/release-camouwolf.sh names the archive."""
        return f"{OS_SHORT[self.os]}.{self.arch}"


# The targets camoufox releases, so a launcher finds a build wherever it finds
# one of camoufox's. A target goes back to disabled, with its reason, only when
# its build is broken.
TARGETS: Dict[str, Target] = {t.name: t for t in (
    Target("linux", "x86_64", True, ""),
    Target("linux", "arm64", True, ""),
    Target("windows", "x86_64", True, ""),
    Target("windows", "i686", True, ""),
    Target("macos", "x86_64", True, ""),
    Target("macos", "arm64", True, ""),
)}


# The target tests.yml builds and runs every browser suite against.
TESTED = "linux-x86_64"


def release_matrix() -> List[Dict[str, str]]:
    """The enabled targets release.yml builds itself: all but TESTED."""
    return [{"target": t.os, "arch": t.arch} for t in TARGETS.values() if t.enabled and t.name != TESTED]


def get(os_name: str, arch: str = "x86_64") -> Target:
    key = f"{os_name}-{arch}"
    if key not in TARGETS:
        raise SystemExit(f"unknown target {key!r}; known: {', '.join(TARGETS)}")
    return TARGETS[key]


def require_enabled(os_name: str, arch: str = "x86_64") -> Target:
    target = get(os_name, arch)
    if not target.enabled:
        raise SystemExit(f"target {target.name} is disabled: {target.disabled_reason}")
    return target


def archive_name(target: Target) -> str:
    """The archive scripts/release-camouwolf.sh writes into browser/dist/."""
    up = read_upstream_sh()
    return f"camouwolf-{up['version']}-{up['release']}-{target.archive_suffix}.zip"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-matrix", action="store_true", help="print release.yml's build matrix as JSON")
    args = parser.parse_args(argv)
    if args.release_matrix:
        print(json.dumps(release_matrix(), separators=(",", ":")))
        return 0
    for t in TARGETS.values():
        print(f"{t.name:15} {'enabled' if t.enabled else 'disabled: ' + t.disabled_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
