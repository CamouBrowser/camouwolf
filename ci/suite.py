#!/usr/bin/env python3
"""Materialise the Playwright suite this run tests against.

A clean, unmodified checkout of upstream playwright-python at the exact tag
`ci/versions.py` resolved for the Chromium being tested, plus an interpreter
holding that same version of the client library and the launcher's own
dependencies. Nothing here is committed; it is rebuilt per run, which is what
"test against the suite for this browser" has to mean if the result is going to
be trustworthy.

On top of that, `overlay()` copies `tests/playwright/` in -- Camouwolf's own
tests, for behaviour upstream has no equivalent for or asserts the opposite of
on purpose. They run against upstream's own conftest and server rather than a
frozen copy of them, so they cannot drift out of step with the suite around
them. See tests/playwright/README.md.

Run:
    python3 -m ci.suite --tag v1.62.0
    python3 -m ci.suite --tag v1.62.0 --python ~/.cache/camouwolf-venv/bin/python
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path
from typing import List, Optional

from ._util import REPO_ROOT, WORK_DIR, die, log, run, work_dir, write_json
from ._util import set_output

TARBALL = "https://github.com/microsoft/playwright-python/archive/refs/tags/{tag}.tar.gz"
CAMOUWOLF_TESTS = REPO_ROOT / "tests" / "playwright"
PYPROJECT = REPO_ROOT / "python" / "pyproject.toml"
# Names overlaid last time, so a reused checkout can tell our files from
# upstream's without guessing from the name.
OVERLAY_MANIFEST = ".camouwolf-overlay.json"
# An interpreter to run the suite with instead of building a venv. It must hold
# the client release the tag names; `check_interpreter` refuses one that does not.
PYTHON_ENV = "CI_SUITE_PYTHON"

# What the suite imports beyond the client, when its own requirements file is
# missing from the checkout.
_MINIMAL = (
    "pytest", "pytest-asyncio", "pytest-timeout", "pytest-rerunfailures", "pytest-repeat",
    "pytest-xdist", "Pillow", "pixelmatch", "autobahn==23.1.2", "twisted", "pyOpenSSL",
    "service_identity", "objgraph", "asyncio-atexit", "requests",
)
# Modules a reused interpreter must be able to import: the suite's, the
# plugin's, and the launcher's.
_REQUIRED_MODULES = (
    "pytest", "pytest_asyncio", "pytest_timeout", "pytest_rerunfailures", "PIL", "pixelmatch",
    "autobahn", "twisted", "OpenSSL", "objgraph", "yaml", "orjson", "fpgen",
)


def fetch(tag: str, dest: Path) -> Path:
    """Download and extract playwright-python at `tag`."""
    dest.mkdir(parents=True, exist_ok=True)
    checkout = dest / f"playwright-python-{tag}"
    if (checkout / "tests").is_dir():
        log(f"reusing existing checkout {checkout}")
        return checkout

    archive = dest / f"{tag}.tar.gz"
    url = TARBALL.format(tag=tag)
    log(f"fetching {url}")
    with urllib.request.urlopen(url, timeout=180) as resp:  # noqa: S310
        archive.write_bytes(resp.read())

    staging = dest / f"_extract-{tag}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        # Refuse path traversal rather than trusting the archive.
        for member in tar.getmembers():
            target = (staging / member.name).resolve()
            if not str(target).startswith(str(staging.resolve())):
                die(f"refusing to extract {member.name}: escapes the staging directory")
        tar.extractall(staging)  # noqa: S202 -- members validated above

    roots = [p for p in staging.iterdir() if p.is_dir()]
    if len(roots) != 1:
        die(f"unexpected archive layout for {tag}: {[p.name for p in roots]}")
    shutil.rmtree(checkout, ignore_errors=True)
    roots[0].rename(checkout)
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    log(f"upstream suite at {checkout}")
    return checkout


def launcher_requirements(pyproject: Optional[Path] = None) -> List[str]:
    """What the in-tree launcher imports, from python/pyproject.toml.

    The suite launches through `python/src`, not through an installed
    `camouwolf`, so its interpreter needs the launcher's dependencies and not
    the package. Playwright is left out: the suite pins its own.
    """
    data = tomllib.loads((pyproject or PYPROJECT).read_text(encoding="utf-8"))
    deps = data["tool"]["poetry"]["dependencies"]
    out: List[str] = []
    for name, spec in deps.items():
        if name in ("python", "playwright"):
            continue
        version = spec.get("version", "*") if isinstance(spec, dict) else spec
        if version == "*":
            out.append(name)
        elif version.startswith("^"):
            major = version[1:].split(".")[0]
            out.append(f"{name}>={version[1:]},<{int(major) + 1}")
        elif version[0].isdigit():
            out.append(f"{name}=={version}")
        else:
            out.append(f"{name}{version}")
    return out


def check_interpreter(python: Path, tag: str) -> List[str]:
    """Why `python` cannot run the suite at `tag`; empty when it can."""
    probe = (
        "import importlib, json, playwright\n"
        "from importlib.metadata import version\n"
        "missing = []\n"
        f"for name in {list(_REQUIRED_MODULES)!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception:\n"
        "        missing.append(name)\n"
        "print(json.dumps({'playwright': version('playwright'), 'missing': missing}))\n"
    )
    proc = run([str(python), "-c", probe])
    if not proc.ok:
        return [f"{python} cannot import playwright: {proc.stderr.strip()[-300:]}"]
    found = json.loads(proc.stdout.strip().splitlines()[-1])
    problems = []
    if found["playwright"] != tag.lstrip("v"):
        problems.append(
            f"{python} holds playwright {found['playwright']}, and the suite is {tag}. The "
            "tests and the client ship together; a mismatch reads as browser bugs."
        )
    if found["missing"]:
        problems.append(f"{python} cannot import: {', '.join(found['missing'])}")
    return problems


def make_venv(checkout: Path, tag: str, *, reuse: bool = True) -> Path:
    """A virtualenv holding the playwright-python release under test.

    The suite is version-locked to its own client library -- the tests and the
    client ship together and a mismatch shows up as failures that look like
    browser bugs -- so it gets its own environment rather than the runner's.
    """
    venv = checkout / ".venv"
    python = venv / "bin" / "python"
    if reuse and python.exists():
        log(f"reusing venv {venv}")
        return python

    run([sys.executable, "-m", "venv", str(venv)], check=True)
    # pip 26.3 rejects a PyPI package whose metadata names a direct-URL
    # dependency even under an extra nobody installs, and the suite's pinned
    # autobahn 23.1.2 has one (web3 from git, extra "all"). 26.2 installs it.
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip<26.3"], check=True)

    # Install the suite's own pins: a hand-picked pytest-asyncio that does not
    # match the suite errors every test at setup with ScopeMismatch, which
    # reads as a broken browser.
    requirements = checkout / "local-requirements.txt"
    if requirements.exists():
        run([str(python), "-m", "pip", "install", "--quiet", "-r", str(requirements)], check=True)
    else:
        log(f"{requirements} is missing; falling back to a minimal set", level="WARN")
        run([str(python), "-m", "pip", "install", "--quiet", *_MINIMAL], check=True)

    # The client the suite is written against, which its own requirements file
    # deliberately does not pin -- it is the package under test upstream.
    version = tag.lstrip("v")
    run([str(python), "-m", "pip", "install", "--quiet", f"playwright=={version}"], check=True)

    # ci/pw_camouwolf_plugin.py reads ci/skiplist.yml and launches through
    # python/src from inside this interpreter.
    run([str(python), "-m", "pip", "install", "--quiet", "PyYAML>=6.0", *launcher_requirements()],
        check=True)

    # No `playwright install chromium`: every launch names the binary under
    # test, and a stock Chromium on the runner is one more browser a broken
    # redirect could silently test instead. ffmpeg alone, for the video tests.
    run([str(venv / "bin" / "playwright"), "install", "ffmpeg"], timeout=600)
    return python


def unshadow(checkout: Path) -> None:
    """Stop the checkout's own `playwright/` directory shadowing the wheel.

    pytest runs with the repository root on `sys.path`, so `import playwright`
    resolves to the *source* tree rather than the installed release. That copy
    has no generated `_repo_version.py` and no bundled Node driver, so every
    test dies at collection with

        ModuleNotFoundError: No module named 'playwright._repo_version'

    which reads like a broken install and is not. Moving it aside makes the
    suite import the real, installed client -- which is what we want to be
    testing against anyway.
    """
    source = checkout / "playwright"
    if not source.is_dir():
        return
    if (source / "_repo_version.py").exists():
        return  # a built checkout; leave it alone
    shadowed = checkout / "_playwright_src"
    shutil.rmtree(shadowed, ignore_errors=True)
    source.rename(shadowed)
    log("moved the checkout's playwright/ aside so the installed wheel is used")


def overlay(checkout: Path) -> List[str]:
    """Copy Camouwolf's own tests into the upstream checkout's async suite.

    They land beside upstream's modules so they pick up its `conftest.py` and
    `tests/server.py` -- the same fixtures, the same test server, at the same
    tag -- instead of needing a frozen copy of the harness to import from.

    A name that already exists upstream is refused rather than overwritten. The
    quiet version of that mistake is shadowing an upstream module and silently
    dropping every test in it, which looks like a smaller suite and nothing
    else. Files this function wrote on a previous run are recorded, so reusing a
    checkout overwrites our own copies and does not mistake them for upstream's.
    """
    target = checkout / "tests" / "async"
    if not target.is_dir():
        die(f"no tests/async/ in {checkout}; the archive layout changed")

    marker = checkout / OVERLAY_MANIFEST
    try:
        previous = set(json.loads(marker.read_text()))
    except (OSError, ValueError):
        previous = set()

    copied: List[str] = []
    for source in sorted(CAMOUWOLF_TESTS.glob("test_*.py")):
        destination = target / source.name
        if destination.exists() and source.name not in previous:
            die(
                f"{source.name} already exists in upstream's suite. Rename the Camouwolf "
                "copy -- overwriting it would silently drop upstream's tests."
            )
        shutil.copy2(source, destination)
        copied.append(source.name)

    # A test renamed or deleted here must not linger in a reused checkout,
    # where it would keep passing against code that no longer claims it.
    for stale in sorted(previous - set(copied)):
        (target / stale).unlink(missing_ok=True)
        log(f"removed stale overlay {stale}")

    marker.write_text(json.dumps(sorted(copied), indent=2))
    log(f"overlaid {len(copied)} Camouwolf test module(s): {', '.join(copied) or 'none'}")
    return copied


def prepare(
    tag: str, *, work: Optional[Path] = None, reuse: bool = True, python: Optional[Path] = None
) -> dict:
    work_dir()
    work = work or (WORK_DIR / "upstream-suite")
    checkout = fetch(tag, work)
    override = python or (Path(os.environ[PYTHON_ENV]) if os.environ.get(PYTHON_ENV) else None)
    if override:
        # Not resolved: a venv's python is a symlink to the base interpreter,
        # and following it leaves the venv.
        python = Path(os.path.abspath(override.expanduser()))
        problems = check_interpreter(python, tag)
        if problems:
            die("; ".join(problems))
        log(f"running the suite with {python}")
    else:
        python = make_venv(checkout, tag, reuse=reuse)
    unshadow(checkout)
    camouwolf_tests = overlay(checkout)

    manifest = {
        "tag": tag,
        "checkout": str(checkout),
        "python": str(python),
        "tests": str(checkout / "tests"),
        "camouwolf_tests": camouwolf_tests,
        "test_files": sorted(p.name for p in (checkout / "tests" / "async").glob("test_*.py")),
    }
    write_json(work / "manifest.json", manifest)
    log(
        f"suite ready: {len(manifest['test_files'])} async test modules at {tag} "
        f"({len(camouwolf_tests)} of them Camouwolf's own)"
    )
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="playwright-python tag, e.g. v1.62.0")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--python", type=Path, help=f"run the suite with this interpreter (${PYTHON_ENV})")
    parser.add_argument("--fresh", action="store_true", help="rebuild the venv from scratch")
    args = parser.parse_args(argv)
    manifest = prepare(args.tag, work=args.work, reuse=not args.fresh, python=args.python)
    # The interpreter the other browser stages reuse (--python / $CI_SUITE_PYTHON).
    set_output("python", str(manifest["python"]))
    set_output("checkout", str(manifest["checkout"]))
    print(manifest["python"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
