"""Find the browser processes this test run started, and only those.

Other runs on the machine may be using the same binary at the same time, so
"every process running this executable" is not ours to judge. Each launch made
here carries a token switch on its command line. Chromium passes neither
unknown switches nor a readable environment on to its children (it overwrites
that memory with the process title), so the token finds the main processes and
the rest are their descendants, snapshotted while the browser is alive.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

import psutil

from ci.browser import launcher_options

TOKEN = f"--ci-native-run={os.getpid()}-{uuid.uuid4().hex}"


def options(binary: Path, **kwargs: Any) -> Dict[str, Any]:
    """Launcher options for `binary`, marked as this run's."""
    return launcher_options(binary, args=[TOKEN, *kwargs.pop("args", [])], **kwargs)


def _alive(proc: psutil.Process) -> bool:
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def main_processes() -> List[psutil.Process]:
    """The browser (not child) processes of launches `options()` marked."""
    found = []
    for proc in psutil.process_iter():
        try:
            if TOKEN in proc.cmdline() and _alive(proc):
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def browser_processes() -> List[psutil.Process]:
    """Every live process of this run's browsers: the mains and their descendants."""
    seen: Dict[int, psutil.Process] = {}
    for main in main_processes():
        seen[main.pid] = main
        try:
            for child in main.children(recursive=True):
                seen[child.pid] = child
        except psutil.NoSuchProcess:
            continue
    return [proc for proc in seen.values() if _alive(proc)]


def wait_gone(tracked: Iterable[psutil.Process], timeout: float = 15.0) -> List[psutil.Process]:
    """Which of `tracked` (and any marked main) still run after `timeout`; empty as soon as none do."""
    tracked = list(tracked)
    deadline = time.monotonic() + timeout
    while True:
        left = {p.pid: p for p in [*tracked, *main_processes()] if _alive(p)}
        if not left or time.monotonic() > deadline:
            return list(left.values())
        time.sleep(0.25)
