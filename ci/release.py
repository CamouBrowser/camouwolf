#!/usr/bin/env python3
"""Release planning: what gets published, under which version.

Three things ship from this repository, and release.yml asks this module about
each so the workflow holds no version arithmetic of its own:

  the browser   one archive per enabled target, on a GitHub release tagged
                `v<chromium version>-<release>` (browser/upstream.sh)
  the launcher  the PyPI package `camouwolf` (python/pyproject.toml)
  the launcher  the npm package `camouwolf` (typescript/package.json)

The two packages carry one version: they are ports of each other and a user
should be able to say "0.3.1" without saying which. `check` fails when the
checked-in versions differ.

Two channels:

  prerelease  every release run that is not a stable one. The checked-in
              version X.Y.Z becomes X.Y.Z.devN on PyPI and X.Y.Z-dev.N on npm,
              N one past the highest either registry has for X.Y.Z.
  stable      X.Y.Z exactly. Refused if either registry already has it: a
              registry version is immutable, so the fix is a version bump.

Nothing has been published yet, so both registries answer "no such package";
that is read as an empty version list, not as an error.

Commands (each prints `key=value` lines and writes them to $GITHUB_OUTPUT):
    python3 -m ci.release check
    python3 -m ci.release browser-plan
    python3 -m ci.release lib-plan --channel prerelease
    python3 -m ci.release stamp --py-version 0.1.0.dev1 --npm-version 0.1.0-dev.1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.error
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from ._util import REPO_ROOT, http_json, read_upstream_sh, run, set_output
from .targets import TARGETS, archive_name

PYPROJECT = REPO_ROOT / "python" / "pyproject.toml"
PACKAGE_JSON = REPO_ROOT / "typescript" / "package.json"
PYPI_NAME = NPM_NAME = "camouwolf"
STABLE = re.compile(r"^\d+\.\d+\.\d+$")


class LibVersion(NamedTuple):
    base: str  # X.Y.Z
    pypi: str  # PEP 440
    npm: str   # semver


# ---------------------------------------------------------------------------
# pure planning
# ---------------------------------------------------------------------------


def prerelease_numbers(base: str, pypi: Iterable[str], npm: Iterable[str]) -> List[int]:
    """Every N already used for `base` on either registry."""
    found = []
    for version in pypi:
        match = re.fullmatch(re.escape(base) + r"\.dev(\d+)", version)
        if match:
            found.append(int(match.group(1)))
    for version in npm:
        match = re.fullmatch(re.escape(base) + r"-dev\.(\d+)", version)
        if match:
            found.append(int(match.group(1)))
    return found


def plan_prerelease(checked_in: str, pypi: Sequence[str], npm: Sequence[str]) -> LibVersion:
    if not STABLE.match(checked_in):
        raise ValueError(f"checked-in version {checked_in!r} is not X.Y.Z")
    if checked_in in pypi or checked_in in npm:
        raise ValueError(
            f"{checked_in} is already published as a stable release; a prerelease of it would "
            "sort before a version users already have. Bump the checked-in version first."
        )
    number = max(prerelease_numbers(checked_in, pypi, npm), default=0) + 1
    return LibVersion(checked_in, f"{checked_in}.dev{number}", f"{checked_in}-dev.{number}")


def plan_stable(checked_in: str, pypi: Sequence[str], npm: Sequence[str]) -> LibVersion:
    if not STABLE.match(checked_in):
        raise ValueError(f"checked-in version {checked_in!r} is not X.Y.Z")
    taken = [name for name, versions in (("PyPI", pypi), ("npm", npm)) if checked_in in versions]
    if taken:
        raise ValueError(f"{checked_in} is already on {' and '.join(taken)}; registry versions are "
                         "immutable. Bump the checked-in version.")
    return LibVersion(checked_in, checked_in, checked_in)


def browser_tag(upstream: Dict[str, str]) -> str:
    return f"v{upstream['version']}-{upstream['release']}"


# ---------------------------------------------------------------------------
# the tree and the registries
# ---------------------------------------------------------------------------


def checked_in_versions() -> Tuple[str, str]:
    """(python/pyproject.toml's version, typescript/package.json's)."""
    py = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]
    npm = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
    return str(py), str(npm)


def _versions(url: str, key: str) -> List[str]:
    try:
        return sorted((http_json(url) or {}).get(key) or {})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []  # never published
        raise


def registry_versions() -> Tuple[List[str], List[str]]:
    return (
        _versions(f"https://pypi.org/pypi/{PYPI_NAME}/json", "releases"),
        _versions(f"https://registry.npmjs.org/{NPM_NAME}", "versions"),
    )


def problems() -> List[str]:
    """What is wrong with the tree as a thing to release from."""
    found = []
    py, npm = checked_in_versions()
    if py != npm:
        found.append(f"python/pyproject.toml says {py}, typescript/package.json says {npm}; "
                     "the two launchers release under one version")
    if not STABLE.match(py):
        found.append(f"python/pyproject.toml version {py!r} is not X.Y.Z")
    up = read_upstream_sh()
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", up.get("version", "")):
        found.append(f"browser/upstream.sh version {up.get('version')!r} is not a Chromium version")
    if up.get("milestone") != up.get("version", "").split(".")[0]:
        found.append(f"browser/upstream.sh milestone {up.get('milestone')!r} is not the major of {up.get('version')!r}")
    if not up.get("release"):
        found.append("browser/upstream.sh has no release")
    return found


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _emit(values: Dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")
        set_output(key, value)


def cmd_check(_: argparse.Namespace) -> int:
    found = problems()
    for problem in found:
        print(f"release check: {problem}", file=sys.stderr)
    if not found:
        print("release check: the tree is releasable")
    return 1 if found else 0


def cmd_browser_plan(_: argparse.Namespace) -> int:
    up = read_upstream_sh()
    enabled = [t for t in TARGETS.values() if t.enabled]
    _emit({
        "tag": browser_tag(up),
        "version": up["version"],
        "release": up["release"],
        "prerelease": "false" if re.fullmatch(r"\d+", up["release"]) else "true",
        "matrix": json.dumps({"include": [{"target": t.name, "archive": archive_name(t)} for t in enabled]}),
    })
    return 0


def cmd_lib_plan(args: argparse.Namespace) -> int:
    py, npm = checked_in_versions()
    if py != npm:
        raise SystemExit(f"python says {py}, typescript says {npm}; run `python -m ci.release check`")
    pypi_versions, npm_versions = registry_versions()
    try:
        plan = (plan_prerelease if args.channel == "prerelease" else plan_stable)(py, pypi_versions, npm_versions)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _emit({"base": plan.base, "py_version": plan.pypi, "npm_version": plan.npm,
           "npm_dist_tag": "latest" if args.channel == "stable" else "dev"})
    return 0


def stamp(py_version: str, npm_version: str, root: Path = REPO_ROOT) -> None:
    """Write the planned versions into the working copy the packages are built from."""
    pyproject = root / "python" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    new, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{py_version}"', text, count=1)
    if count != 1:
        raise SystemExit("python/pyproject.toml has no `version = \"...\"` line to stamp")
    pyproject.write_text(new, encoding="utf-8")
    package = root / "typescript" / "package.json"
    text = package.read_text(encoding="utf-8")
    new, count = re.subn(r'(?m)^(\s*"version":\s*)"[^"]+"', lambda m: f'{m.group(1)}"{npm_version}"', text, count=1)
    if count != 1:
        raise SystemExit("typescript/package.json has no \"version\" field to stamp")
    package.write_text(new, encoding="utf-8")


def cmd_stamp(args: argparse.Namespace) -> int:
    stamp(args.py_version, args.npm_version)
    run(["git", "diff", "--stat", "--", "python/pyproject.toml", "typescript/package.json"], cwd=REPO_ROOT, tee=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("browser-plan").set_defaults(fn=cmd_browser_plan)
    plan = sub.add_parser("lib-plan")
    plan.add_argument("--channel", choices=["prerelease", "stable"], required=True)
    plan.set_defaults(fn=cmd_lib_plan)
    stamp_parser = sub.add_parser("stamp")
    stamp_parser.add_argument("--py-version", required=True)
    stamp_parser.add_argument("--npm-version", required=True)
    stamp_parser.set_defaults(fn=cmd_stamp)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
