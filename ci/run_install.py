#!/usr/bin/env python3
"""Install a packaged build the way a user gets it, and prove it starts.

Unpacks the archive ci.run_build made (or takes an unpacked build with
`--binary`), and checks what a broken package gets wrong:

  layout        the archive holds one directory with the `camouwolf` binary,
                executable, and a version.json beside it
  version.json  names the Chromium version, release and milestone of
                browser/upstream.sh (or `--expect-version`)
  --version     the binary reports that Chromium version
  launch        the launcher starts it, a page loads, and navigator.userAgent
                carries the milestone

The binary's path is written to $GITHUB_OUTPUT as `binary`, which is what the
browser stages after this one are given.

Run:
    python3 -m ci.run_install --package browser/dist/camouwolf-...-lin.x86_64.zip --dest .ci-work/install
    python3 -m ci.run_install --binary path/to/camouwolf
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import results
from ._util import RESULTS_DIR, read_upstream_sh, run, set_output, work_dir
from .browser import launcher_options, private_display

BINARY_NAME = "camouwolf"


def unpack(package: Path, dest: Path) -> Path:
    """Extract `package` into an empty `dest`, keeping modes. Returns the binary."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError(f"{package.name}: entry escapes the archive: {info.filename}")
            archive.extract(info, dest)
            mode = info.external_attr >> 16
            if mode and not info.is_dir():
                os.chmod(target, mode & 0o777)
    tops = [p for p in dest.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        raise ValueError(f"{package.name}: expected one top-level directory, found {[p.name for p in tops]}")
    return tops[0] / BINARY_NAME


def launch_check(binary: Path, milestone: str) -> str:
    """Why the launch check failed, or '' when the build starts and names its milestone."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with private_display(), sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launcher_options(binary))
        try:
            page = browser.new_page()
            page.goto("data:text/html,<title>ok</title>")
            agent = page.evaluate("navigator.userAgent")
            title = page.title()
        finally:
            browser.close()
    if title != "ok":
        return f"the page did not load (title {title!r})"
    if f"Chrome/{milestone}." not in agent:
        return f"navigator.userAgent does not carry milestone {milestone}: {agent}"
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", type=Path, help="the archive to unpack")
    source.add_argument("--binary", type=Path, help="an already unpacked build")
    parser.add_argument("--dest", type=Path, help="where to unpack (default .ci-work/install)")
    parser.add_argument("--expect-version", help="Chromium version, if not browser/upstream.sh's")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    result = results.GateResult(gate="install")
    up = read_upstream_sh()
    version = args.expect_version or up["version"]
    milestone = version.split(".")[0]

    def record(name: str, problem: str) -> None:
        result.record(name, results.FAIL if problem else results.PASS)
        if problem:
            result.note(f"{name}: {problem}")

    try:
        binary = unpack(args.package, args.dest or work_dir() / "install") if args.package else args.binary.resolve()
        record("layout", "" if binary.is_file() and os.access(binary, os.X_OK)
               else f"{binary} is missing or not executable")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        record("layout", str(exc))
        result.finish(results.FAIL).save(args.results_dir)
        return 1
    if result.tests.get("layout") == results.PASS:
        try:
            import json  # noqa: PLC0415

            meta: Dict[str, Any] = json.loads((binary.parent / "version.json").read_text(encoding="utf-8"))
            wanted = {"version": version, "milestone": int(milestone)}
            if not args.expect_version:
                wanted["release"] = up["release"]
            wrong = {k: (meta.get(k), v) for k, v in wanted.items() if meta.get(k) != v}
            record("version.json", f"(found, expected): {wrong}" if wrong else "")
        except (OSError, ValueError) as exc:
            record("version.json", f"unreadable: {exc}")

        proc = run([str(binary), "--version"], timeout=60)
        reported = re.search(r"\d+\.\d+\.\d+\.\d+", proc.stdout)
        record("--version", "" if proc.ok and reported and reported.group(0) == version
               else f"exit {proc.code}, printed {proc.stdout.strip()[:120]!r}, expected {version}")
        try:
            record("launch", launch_check(binary, milestone))
        except Exception as exc:  # noqa: BLE001 -- a launch that throws is the failure
            record("launch", f"{type(exc).__name__}: {str(exc)[:400]}")

    result.metrics.update(version=version, source="package" if args.package else "binary")
    tally = result.tally()
    ok = tally.get("fail", 0) == 0 and tally.get("total", 0) == 4
    if ok:
        set_output("binary", str(binary))
    result.note(f"{tally.get('pass', 0)} of 4 install checks passed")
    result.finish(results.PASS if ok else results.FAIL).save(args.results_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
