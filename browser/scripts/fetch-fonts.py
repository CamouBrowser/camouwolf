#!/usr/bin/env python3
"""Fetch camoufox's font bundle and unpack it into browser/bundle/fonts/.

ONE BUNDLE FOR BOTH BROWSERS
----------------------------
The font set is a property of the operating system being claimed, not of the
browser claiming it. camouwolf therefore ships camoufox's bundle byte for byte:
`data/font-bundle.json` is a copy of camoufox's
browser/scripts/data/font-bundle.json (same release asset, same sha256), and
`scripts/gen-bundle-fonts.py --check` fails CI if the two drift.

The bundle is split by which OSes read each file (L, LM, LMW, LW, M, MW, W;
`fonts/groups.json` maps each OS to its groups). The launcher points a persona's
fontconfig at its groups only, so a Windows identity never sees a macOS-only
file. The families each persona can resolve from this bundle are recorded in
python/src/camouwolf/data/bundle-fonts.json (scripts/gen-bundle-fonts.py), and
the launcher claims nothing outside that list.

WHERE THE BYTES LIVE
--------------------
The archive (~840 MB .tar.xz) is a release asset on daijro/camoufox, not repo
content. It is kept in a cache outside the checkout (CAMOUWOLF_FONT_CACHE,
default ~/.cache/camouwolf/fonts) so a fresh clone or a cleaned CI workspace
does not download it again. The download is rate-capped
(CAMOUWOLF_FONT_RATE, default 10M per second) so it leaves the shared line headroom.

Usage:
    python3 browser/scripts/fetch-fonts.py            # download + verify the archive
    python3 browser/scripts/fetch-fonts.py --extract  # ...and unpack to bundle/fonts/
    python3 browser/scripts/fetch-fonts.py --check    # verify the extracted tree only
    python3 browser/scripts/fetch-fonts.py --clean    # drop the extracted directory
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

BROWSER = Path(__file__).resolve().parent.parent
SPEC = BROWSER / "scripts" / "data" / "font-bundle.json"
BUNDLE_DIR = BROWSER / "bundle"
FONTS_DIR = BUNDLE_DIR / "fonts"
STAMP = FONTS_DIR / ".bundle-sha256"
DEFAULT_CACHE = Path.home() / ".cache" / "camouwolf" / "fonts"


def load_spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def cache_dir() -> Path:
    return Path(os.environ.get("CAMOUWOLF_FONT_CACHE") or DEFAULT_CACHE).expanduser()


def digest(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify(spec: dict, path: Path) -> bool:
    """True when `path` is the archive this commit pins."""
    if not path.is_file():
        return False
    size = path.stat().st_size
    if spec.get("size") and size != spec["size"]:
        print(f"size mismatch: have {size}, expected {spec['size']}", file=sys.stderr)
        return False
    got = digest(path)
    if got != spec["sha256"]:
        print(f"sha256 mismatch:\n  have     {got}\n  expected {spec['sha256']}", file=sys.stderr)
        return False
    return True


def extracted_sha() -> str | None:
    if not (FONTS_DIR / "groups.json").is_file():
        return None
    try:
        return STAMP.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def download(spec: dict, path: Path) -> None:
    rate = os.environ.get("CAMOUWOLF_FONT_RATE", "10M")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    print(f"fetching {spec['url']} (capped at {rate}/s, {spec['size'] / 1e6:.0f} MB)", file=sys.stderr)
    if not shutil.which("curl"):
        sys.exit("curl is needed to download the font bundle")
    # -C - resumes a .part left by an interrupted run.
    cmd = ["curl", "-fL", "--retry", "3", "-C", "-", "--limit-rate", rate, "-o", str(tmp), spec["url"]]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("font bundle download failed")
    os.replace(tmp, path)


def extract(spec: dict, archive: Path) -> None:
    if FONTS_DIR.exists():
        shutil.rmtree(FONTS_DIR)
    print(f"extracting {archive.name} -> {FONTS_DIR}", file=sys.stderr)
    root = BUNDLE_DIR.resolve()
    with tarfile.open(archive, "r:xz") as tf:
        for member in tf.getmembers():
            dest = (BUNDLE_DIR / member.name).resolve()
            if dest != FONTS_DIR.resolve() and FONTS_DIR.resolve() not in dest.parents:
                sys.exit(f"refusing to extract outside bundle/fonts/: {member.name}")
            if not (member.isfile() or member.isdir()):
                sys.exit(f"refusing non-regular member: {member.name}")
        tf.extractall(BUNDLE_DIR)
    # Strays the v1 archive carries from camoufox's tree; camoufox prunes them too.
    for stray in ("000_README.txt", "cleanfonts.sh"):
        (FONTS_DIR / stray).unlink(missing_ok=True)
    # Written last: a crash mid-extract leaves no stamp, so the tree is redone.
    STAMP.write_text(spec["sha256"] + "\n", encoding="utf-8")
    n = sum(len(files) for _, _, files in os.walk(FONTS_DIR))
    print(f"extracted {n} files", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", action="store_true", help="unpack to bundle/fonts/ after verifying")
    ap.add_argument("--check", action="store_true", help="exit non-zero unless bundle/fonts/ holds the pinned bundle")
    ap.add_argument("--clean", action="store_true", help="remove bundle/fonts/")
    ap.add_argument("--force", action="store_true", help="re-download even if the cached archive verifies")
    args = ap.parse_args()
    spec = load_spec()

    if args.clean:
        shutil.rmtree(FONTS_DIR, ignore_errors=True)
        return 0
    if args.check:
        have = extracted_sha()
        if have != spec["sha256"]:
            print(f"bundle/fonts/ is {'absent' if have is None else 'from ' + have}; "
                  f"expected {spec['sha256']} ({spec['asset']})", file=sys.stderr)
            return 1
        return 0
    if args.extract and not args.force and extracted_sha() == spec["sha256"]:
        print(f"bundle/fonts/ already holds {spec['asset']}", file=sys.stderr)
        return 0

    archive = cache_dir() / spec["asset"]
    if args.force or not verify(spec, archive):
        download(spec, archive)
        if not verify(spec, archive):
            archive.unlink(missing_ok=True)
            sys.exit("the downloaded archive does not match font-bundle.json")
    print(f"{archive} verified", file=sys.stderr)
    if args.extract:
        extract(spec, archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
