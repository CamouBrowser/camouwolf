#!/usr/bin/env python3
"""Build the browser and package it.

Two steps, each recorded:

  build     browser/scripts/build.sh with CAMOUWOLF_STAGES (default
            "sync patch build"; "build" alone after ci.run_prepare)
  fonts     browser/scripts/fetch-fonts.py --extract: camoufox's pinned font
            bundle, cached in CAMOUWOLF_FONT_CACHE; a package without it
            cannot render the fonts its identities claim
  package   scripts/release-camouwolf.sh --skip-build --no-release, which
            writes browser/dist/camouwolf-<version>-<release>-<target>.zip and
            its .sha256

The gate passes when the archive and its checksum exist and agree. Nothing is
uploaded from here; publishing is release.yml's job.

Run (on a build machine: hours, and about 100 GB):
    python3 -m ci.run_build
    python3 -m ci.run_build --stages build --jobs 16
    python3 -m ci.run_build --dry-run
    python3 -m ci.run_build --target macos --arch arm64 --package-only
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import List, Optional

from . import results
from ._util import BROWSER_ROOT, REPO_ROOT, RESULTS_DIR, read_upstream_sh, run, set_output
from .run_prepare import BUILD_SH, build_env
from .targets import archive_name, require_enabled

PACKAGE_SH = REPO_ROOT / "scripts" / "release-camouwolf.sh"
FETCH_FONTS = BROWSER_ROOT / "scripts" / "fetch-fonts.py"
DIST = BROWSER_ROOT / "dist"


def checksum_ok(archive: Path) -> bool:
    """The .sha256 beside `archive` names the archive's own digest."""
    sidecar = archive.with_name(archive.name + ".sha256")
    if not archive.is_file() or not sidecar.is_file():
        return False
    digest = hashlib.sha256()
    with archive.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return sidecar.read_text(encoding="utf-8").split()[0] == digest.hexdigest()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="linux")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--stages", default="sync patch build", help="CAMOUWOLF_STAGES for build.sh")
    parser.add_argument("--jobs", type=int, help="CAMOUWOLF_NINJA_JOBS")
    parser.add_argument("--no-package", action="store_true", help="stop after the build")
    parser.add_argument("--package-only", action="store_true",
                        help="package an out/ directory a chain of build jobs already finished")
    parser.add_argument("--timeout", type=int, default=12 * 3600)
    parser.add_argument("--dry-run", action="store_true", help="print what would run")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    target = require_enabled(args.target, args.arch)
    env = build_env(args.stages, target, args.jobs)
    build_cmd = ["bash", str(BUILD_SH)]
    fonts_cmd = [sys.executable, str(FETCH_FONTS), "--extract"]
    package_cmd = ["bash", str(PACKAGE_SH), "--target", target.os, "--arch", target.arch, "--skip-build", "--no-release"]
    archive = DIST / archive_name(target)
    if args.dry_run:
        print(" ".join(f"{k}={v!r}" for k, v in env.items()), " ".join(build_cmd))
        if not args.no_package:
            print(" ".join(fonts_cmd))
            print(" ".join(package_cmd))
            print(f"expects {archive}")
        return 0

    result = results.GateResult(gate="build")
    result.metrics.update(read_upstream_sh(), target=target.name, stages=args.stages)

    if not args.package_only:
        build = run(build_cmd, cwd=REPO_ROOT, env=env, timeout=args.timeout, tee=True, capture=False)
        result.record("build", results.PASS if build.ok else results.FAIL)
        if not build.ok:
            result.note(f"browser/scripts/build.sh exited {build.code} (stages: {args.stages})")
            result.finish(results.FAIL).save(args.results_dir)
            return 1
    if args.no_package:
        result.finish(results.PASS).save(args.results_dir)
        return 0

    fonts = run(fonts_cmd, cwd=REPO_ROOT, timeout=1800, tee=True, capture=False)
    result.record("fonts", results.PASS if fonts.ok else results.FAIL)
    if not fonts.ok:
        result.note(f"fetch-fonts.py --extract exited {fonts.code}: the pinned font bundle could not be "
                    "fetched or did not verify")
        result.finish(results.FAIL).save(args.results_dir)
        return 1

    package = run(package_cmd, cwd=REPO_ROOT, timeout=3600, tee=True, capture=False)
    packaged = package.ok and checksum_ok(archive)
    result.record("package", results.PASS if packaged else results.FAIL)
    if not packaged:
        result.note(f"scripts/release-camouwolf.sh exited {package.code}; "
                    f"{archive.name} {'exists' if archive.is_file() else 'is missing'}, "
                    "and the gate needs it with a matching .sha256")
        result.finish(results.FAIL).save(args.results_dir)
        return 1
    result.metrics.update(archive=archive.name, archive_bytes=archive.stat().st_size)
    result.artifacts.append(archive.name)
    set_output("archive", str(archive))
    result.finish(results.PASS).save(args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
