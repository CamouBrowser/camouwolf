"""pytest plugin that lets upstream's own Playwright suite drive a Camouwolf build.

Loaded with `-p ci.pw_camouwolf_plugin` against an *unmodified* checkout of
playwright-python's tests, so upstream can refactor its conftest freely without
breaking us. Three jobs:

1. **Launch the build under test, through the launcher.** Upstream's
   `launch_arguments` fixture has no way to select a binary, so launches are
   intercepted at the implementation layer instead, and every one is given what
   `camouwolf.launch_options()` returns for that binary: its switches, the
   default switches it removes, and the identity in the environment. A test
   that passes its own `args`, `env` or `executablePath` keeps them, on top of
   the launcher's. Hooking `_impl` rather than the fixture is what makes this
   survive upstream reshuffling its fixtures. If no binary is given the plugin
   refuses to start, because the alternative is silently testing a downloaded
   stock Chromium and reporting a meaningless pass.

   There is no second "world" to choose here. On Chromium the launcher leaves
   Playwright's evaluation where upstream expects it, so the suite runs once, as
   shipped. Upstream's `RemoteServer` launches in a child process, so its
   config is given the same options (`install_remote_server`).

2. **Apply `ci/skiplist.yml`.** Deselects, rather than xfails, the tests
   Camouwolf cannot pass by design. Deselection keeps them out of the totals
   entirely, so the pass rate means something.

3. **Shard.** `CI_SHARD=i/n` keeps a deterministic slice, so the suite spreads
   across parallel runners. Sharding by a hash of the node id rather than by
   position means a shard's contents do not shift when upstream adds a test in
   the middle of a file.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_EXECUTABLE_ENV = "CAMOUWOLF_BINARY"
_SHARD_ENV = "CI_SHARD"
_SKIPLIST_ENV = "CI_SKIPLIST"
# The identity every launch of the run draws for. One OS, so a failure does not
# depend on which OS a launch happened to draw; ci/build-tester.yml is where
# the other personas are exercised.
_OS_ENV = "CI_IDENTITY_OS"


# ---------------------------------------------------------------------------
# skiplist
# ---------------------------------------------------------------------------


def skiplist_path(path: Optional[Path] = None) -> Path:
    """Where the skiplist is read from: $CI_SKIPLIST, else next to this file."""
    if path is not None:
        return path
    # Path("") is Path("."), which exists and is a directory -- so an unset
    # CI_SKIPLIST must be treated as unset, not as a path.
    override = os.environ.get(_SKIPLIST_ENV, "").strip()
    return Path(override) if override else Path(__file__).resolve().parent / "skiplist.yml"


def load_skiplist(path: Optional[Path] = None) -> List[Dict[str, str]]:
    path = skiplist_path(path)
    if not path.is_file():
        print(f"camouwolf: skiplist not found at {path}; running with no skips")
        return []
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover -- environment problem
        raise RuntimeError(
            "PyYAML is missing from the interpreter running this suite, so "
            f"{path} cannot be read. Refusing to continue: without the skiplist "
            "the tests Camouwolf cannot pass by design would run and fail, "
            "which looks like a broken browser rather than a broken environment."
        ) from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = []
    for entry in data.get("skip") or []:
        if not isinstance(entry, dict):
            continue
        if not str(entry.get("reason", "")).strip():
            # Mirrors ci/summarize.py, which fails the run on an unreasoned
            # entry. Ignoring it here would let one skip tests silently.
            raise RuntimeError(f"skiplist entry has no reason: {entry!r}")
        entries.append(entry)
    return entries


def skip_reason(nodeid: str, entries: List[Dict[str, str]]) -> Optional[str]:
    """The reason this node id is skipped, or None to run it."""
    # Node ids arrive as `...[chromium]`; matching the bare id too keeps a
    # `test:` entry copied from a failure report from being a silent no-op.
    base = nodeid.partition("[")[0]
    # pytest node ids are posix-style even on Windows.
    for entry in entries:
        if "module" in entry:
            module = str(entry["module"]).lstrip("./")
            if nodeid.startswith(module + "::") or nodeid == module:
                return str(entry["reason"])
        elif "test" in entry:
            target = str(entry["test"]).lstrip("./")
            if nodeid == target or base == target:
                return str(entry["reason"])
        elif "pattern" in entry:
            if str(entry["pattern"]) in nodeid:
                return str(entry["reason"])
    return None


# ---------------------------------------------------------------------------
# sharding
# ---------------------------------------------------------------------------


def parse_shard(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    """"3/6" -> (3, 6). One-based, like every CI UI that will display it."""
    if not raw:
        return None
    try:
        index, _, total = raw.partition("/")
        shard, count = int(index), int(total)
    except ValueError:
        raise RuntimeError(f"a shard must look like '3/6', got {raw!r}") from None
    if not (1 <= shard <= count):
        raise RuntimeError(f"shard {raw!r} is out of range")
    return shard, count


def shard_of(nodeid: str, count: int) -> int:
    """Stable one-based shard for a node id.

    Hashed rather than positional: adding a test in the middle of a file must
    not reshuffle which shard every later test lands in, or a flake starts
    looking like it moved between runners.
    """
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") % count) + 1


# ---------------------------------------------------------------------------
# browser wiring
# ---------------------------------------------------------------------------


def merge_launch(kwargs: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    """Lay a test's own launch arguments over the launcher's options.

    `kwargs` are Playwright's implementation-layer (camelCase) arguments,
    `options` what `camouwolf.launch_options()` returned. The launcher supplies
    the binary, its switches, the default switches it removes and the identity
    in the environment; whatever the test passed is added, never replaced by
    them. A test that names its own executable keeps it: those tests are about
    the path.
    """
    merged = dict(kwargs)
    if not merged.get("executablePath"):
        merged["executablePath"] = options["executable_path"]
    merged["args"] = [*options.get("args", []), *(merged.get("args") or [])]
    ignored = merged.get("ignoreDefaultArgs")
    if ignored is not True:
        merged["ignoreDefaultArgs"] = list(
            dict.fromkeys([*options.get("ignore_default_args", []), *(ignored or [])])
        )
    merged["env"] = {**options.get("env", {}), **(merged.get("env") or {})}
    if merged.get("headless") is None:
        merged["headless"] = options.get("headless", True)
    return merged


def _install_launcher(binary: Path, os_name: str) -> None:
    from playwright._impl._browser_type import BrowserType

    from .browser import launcher_options

    for name in ("launch", "launch_persistent_context"):
        original = getattr(BrowserType, name, None)
        if original is None or getattr(original, "_camouwolf_wrapped", False):
            continue

        def make(original: Any):  # noqa: ANN401
            async def wrapper(self, *args: Any, **kwargs: Any):  # noqa: ANN401
                if self.name != "chromium":
                    return await original(self, *args, **kwargs)
                # A fresh identity per launch, as a user's launch draws one.
                options = launcher_options(
                    binary, os_name=os_name, headless=kwargs.get("headless") is not False
                )
                return await original(self, *args, **merge_launch(kwargs, options))

            wrapper._camouwolf_wrapped = True  # type: ignore[attr-defined]
            return wrapper

        setattr(BrowserType, name, make(original))


def install_remote_server(binary: Path, os_name: str, modules: Any) -> bool:  # noqa: ANN401
    """Give upstream's `RemoteServer` the launcher's options.

    The `launch_server` fixture runs `cli.js launch-server` in a child process,
    out of reach of the launch wrapper, and the child reads camelCase keys from
    a JSON config. Unwrapped, it starts Playwright's own Chromium. `modules` are
    the loaded conftests; returns whether one held a `RemoteServer`.
    """
    from .browser import launcher_options

    found = False
    for module in modules:
        server = getattr(module, "RemoteServer", None)
        if not isinstance(server, type):
            continue
        found = True
        if getattr(server.__init__, "_camouwolf_wrapped", False):
            continue

        def make(original: Any):  # noqa: ANN401
            def wrapper(self, browser_name: str, launch_server_options: Dict, *args: Any, **kwargs: Any):  # noqa: ANN401
                if browser_name == "chromium":
                    options = launcher_options(
                        binary, os_name=os_name, headless=launch_server_options.get("headless") is not False
                    )
                    launch_server_options = merge_launch(launch_server_options, options)
                return original(self, browser_name, launch_server_options, *args, **kwargs)

            wrapper._camouwolf_wrapped = True  # type: ignore[attr-defined]
            return wrapper

        server.__init__ = make(server.__init__)
    return found


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------


def pytest_configure(config) -> None:  # noqa: ANN001
    executable = os.environ.get(_EXECUTABLE_ENV)
    if not executable:
        raise RuntimeError(
            f"{_EXECUTABLE_ENV} is not set. The upstream conformance suite has no way to "
            "select a browser binary, so without it pytest would silently test a "
            "downloaded stock Chromium and report a meaningless pass."
        )
    from .browser import require_private_display

    require_private_display()
    config._camouwolf_os = os.environ.get(_OS_ENV, "").strip() or "linux"
    _install_launcher(Path(os.path.abspath(executable)), config._camouwolf_os)
    config._camouwolf_skiplist = load_skiplist()
    # Report the file actually read, not where this plugin happens to sit.
    config._camouwolf_skiplist_path = skiplist_path()
    config._camouwolf_shard = parse_shard(os.environ.get(_SHARD_ENV))
    config._camouwolf_skipped: Dict[str, str] = {}


def pytest_collection_modifyitems(config, items) -> None:  # noqa: ANN001
    import sys

    conftests = [m for m in list(sys.modules.values()) if (getattr(m, "__file__", None) or "").endswith("conftest.py")]
    binary = os.environ.get(_EXECUTABLE_ENV)
    if (
        binary
        and not install_remote_server(Path(os.path.abspath(binary)), getattr(config, "_camouwolf_os", "linux"), conftests)
        and any("launch_server" in getattr(item, "fixturenames", ()) for item in items)
    ):
        raise RuntimeError(
            "tests use launch_server but no conftest defines RemoteServer: upstream moved it, "
            "and its server would launch a stock Chromium"
        )
    entries = getattr(config, "_camouwolf_skiplist", [])
    shard = getattr(config, "_camouwolf_shard", None)
    skipped: Dict[str, str] = getattr(config, "_camouwolf_skipped", {})

    keep = []
    deselected = []
    for item in items:
        reason = skip_reason(item.nodeid, entries)
        if reason:
            skipped[item.nodeid] = reason
            deselected.append(item)
            continue
        if shard and shard_of(item.nodeid, shard[1]) != shard[0]:
            deselected.append(item)
            continue
        keep.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = keep


def pytest_report_header(config) -> List[str]:  # noqa: ANN001
    shard = getattr(config, "_camouwolf_shard", None)
    lines = [
        f"camouwolf: binary={os.environ.get(_EXECUTABLE_ENV)}",
        f"camouwolf: launched through camouwolf.launch_options(os={getattr(config, '_camouwolf_os', '?')!r}) "
        f"on DISPLAY={os.environ.get('DISPLAY')}",
        f"camouwolf: {len(getattr(config, '_camouwolf_skiplist', []))} skiplist entries "
        f"from {getattr(config, '_camouwolf_skiplist_path', '(not loaded)')}",
    ]
    if shard:
        lines.append(f"camouwolf: shard {shard[0]} of {shard[1]}")
    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001
    """Say what was skipped and why, so the list stays visible rather than silent."""
    skipped: Dict[str, str] = getattr(config, "_camouwolf_skipped", {})
    if not skipped:
        return
    by_reason: Dict[str, int] = {}
    for reason in skipped.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1
    terminalreporter.write_sep("-", f"camouwolf skiplist: {len(skipped)} test(s) deselected")
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        terminalreporter.write_line(f"  {count:>4}  {' '.join(reason.split())[:150]}")
