"""Carry a finished out/ from one commit's build to the next.

A CI stage gives every source file the same old time before it compiles
(.github/actions/build-stage), so ninja rebuilds only what is newer than out/.
That is right for an out/ from the same commit. An out/ restored from the build
cache comes from another commit, where the overlay -- the patches, additions and
branding apply-patches.sh writes -- may differ. `record` saves a hash of every
overlay file with out/; `stale` gives the current time to each overlay file,
of either commit, whose bytes differ, so ninja rebuilds exactly those. Every
other file is pristine Chromium at the same tag in both.

    python3 -m ci.out_cache record --src <chromium/src>
    python3 -m ci.out_cache stale  --src <chromium/src>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Optional

from ._util import log

OVERLAY_LIST = ".camouwolf-overlay-files"
MANIFEST = "out/.camouwolf-overlay.json"


def _hash(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _overlay(src: Path) -> Dict[str, Optional[str]]:
    names = (src / OVERLAY_LIST).read_text().split()
    return {name: _hash(src / name) for name in names}


def record(src: Path) -> None:
    (src / MANIFEST).write_text(json.dumps(_overlay(src), indent=0, sort_keys=True))


def stale(src: Path) -> int:
    """Touch every overlay file that differs from the recorded build; return how many."""
    manifest = src / MANIFEST
    if not manifest.is_file():
        log("no recorded overlay with out/: nothing to compare")
        return 0
    recorded = json.loads(manifest.read_text())
    current = _overlay(src)
    touched = 0
    for name in sorted(set(recorded) | set(current)):
        path = src / name
        if name not in current:
            current[name] = _hash(path)
        if path.is_file() and recorded.get(name, "absent") != current[name]:
            os.utime(path)
            touched += 1
    log(f"{touched} of {len(current)} overlay files differ from the cached build")
    return touched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["record", "stale"])
    parser.add_argument("--src", type=Path, required=True)
    args = parser.parse_args()
    (record if args.command == "record" else stale)(args.src)


if __name__ == "__main__":
    main()
