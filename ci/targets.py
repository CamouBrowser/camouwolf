"""The build targets, and which of them CI builds.

One table, read by ci.run_prepare, ci.run_build, tests.yml and
`python -m ci.targets`, so "is Windows built?" has one answer. release.yml's
build matrix excludes exactly the disabled rows (ci/tests/test_ci.py checks it).

A target is an OS and a CPU. Every one is cross-compiled on a Linux host
(ci/tribal-rules.yml: releases-on-hosted-runners); docs/CROSS-COMPILING.md
says what each needs.
"""

from __future__ import annotations

import argparse
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


TARGETS: Dict[str, Target] = {t.name: t for t in (
    Target("linux", "x86_64", True, ""),
    Target("linux", "arm64", False, "the cross build has not yet been linked and run on an arm64 host"),
    Target("windows", "x86_64", False, "no Visual Studio toolchain zip has been packed yet, so it has never compiled"),
    Target("windows", "arm64", False, "the launchers have no Windows arm64 build to fetch (OS_ARCH_MATRIX)"),
    Target("macos", "x86_64", False, "the cross build has not yet been linked and run on a Mac"),
    Target("macos", "arm64", False, "the cross build has not yet been linked and run on a Mac"),
)}


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
    parser.parse_args(argv)
    for t in TARGETS.values():
        print(f"{t.name:15} {'enabled' if t.enabled else 'disabled: ' + t.disabled_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
