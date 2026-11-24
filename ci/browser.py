"""Launch the browser under test the way a user's script does: through the launcher.

Every browser stage gets its launch from `camouwolf.launch_options()`, so what
is tested is the pair a user runs -- this tree's launcher and the binary named
on the command line -- and never a raw binary with hand-written switches.

Two rules hold for every launch made from here:

  * **No window on a real desktop.** The run owns an Xvfb (`private_display`),
    `DISPLAY` points at it, `WAYLAND_DISPLAY` is removed, and Chromium is told
    `--ozone-platform=x11` so it cannot pick a Wayland session up by itself.
    That holds for headless launches too: a headless Chromium that falls back
    to a headed one must still land on the private display.
  * **The binary is named by the caller.** Nothing here has a default path.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ._util import REPO_ROOT, log

LAUNCHER_SRC = REPO_ROOT / "python" / "src"
OZONE = "--ozone-platform=x11"


def import_launcher():
    """The in-tree `camouwolf` package, whatever is installed."""
    src = str(LAUNCHER_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    import camouwolf  # noqa: PLC0415

    return camouwolf


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def private_display(size: str = "1920x1080x24") -> Iterator[str]:
    """An Xvfb this run owns. Sets DISPLAY, drops WAYLAND_DISPLAY, restores both.

    `-displayfd` lets the server choose a free number, so two runs side by side
    never race for one. Without Xvfb the run stops: carrying on would leave the
    browser the session's own DISPLAY.
    """
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        raise RuntimeError(
            "Xvfb is not installed. Browser stages run on a private X display so "
            "no window can reach a real desktop; install it (apt install xvfb)."
        )
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [xvfb, "-displayfd", str(write_fd), "-screen", "0", size, "-nolisten", "tcp"],
        pass_fds=(write_fd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    saved = {key: os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY")}
    try:
        with os.fdopen(read_fd) as fh:
            number = fh.readline().strip()
        if not number:
            raise RuntimeError(f"Xvfb exited {proc.poll()} without naming a display")
        display = f":{number}"
        os.environ["DISPLAY"] = display
        os.environ.pop("WAYLAND_DISPLAY", None)
        log(f"private X display {display} ({size})")
        yield display
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def require_private_display() -> str:
    """The DISPLAY a child of `private_display()` inherited, or a refusal."""
    display = os.environ.get("DISPLAY", "")
    if not display or os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError(
            "this process has no private X display (DISPLAY unset, or WAYLAND_DISPLAY "
            "set). Run it through its ci.run_* entry point, which starts one."
        )
    return display


# ---------------------------------------------------------------------------
# launch options
# ---------------------------------------------------------------------------


def launcher_options(
    binary: Path,
    *,
    os_name: Optional[str] = "linux",
    headless: bool = True,
    args: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """`camouwolf.launch_options()` for the binary under test.

    The default addon is left out: these launches must not depend on
    `camouwolf fetch` having installed one. Everything else is the launcher's
    own default, and `kwargs` go to it unchanged.
    """
    import warnings  # noqa: PLC0415

    camouwolf = import_launcher()
    require_private_display()
    kwargs.setdefault("exclude_addons", [camouwolf.DefaultAddons.UBO])
    kwargs.setdefault("i_know_what_im_doing", True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        options = camouwolf.launch_options(
            executable_path=str(binary),
            os=os_name,
            headless=headless,
            args=[OZONE, *(args or [])],
            **kwargs,
        )
    env = options.setdefault("env", {})
    env["DISPLAY"] = os.environ["DISPLAY"]
    env.pop("WAYLAND_DISPLAY", None)
    return options


def claimed_config(options: Dict[str, Any]) -> Dict[str, Any]:
    """The config the launcher put in `options`' environment."""
    from camouwolf.utils import launch_config  # noqa: PLC0415

    return launch_config(options)


def binary_version(binary: Path) -> Optional[str]:
    """The Chromium version a build names in the version.json beside it."""
    import json  # noqa: PLC0415

    try:
        return str(json.loads((binary.parent / "version.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return None
