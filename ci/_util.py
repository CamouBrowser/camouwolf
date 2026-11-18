"""Shared plumbing for the pipeline: paths, subprocess, JSON, hashing, logging.

Deliberately dependency-free (stdlib only) so the early steps can run on a bare
runner before anything is installed.
"""

from __future__ import annotations

import hashlib
import hmac
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, NoReturn, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
# The browser build: upstream.sh, the patch series, the additions, the build scripts.
BROWSER_ROOT = REPO_ROOT / "browser"
CI_DIR = REPO_ROOT / "ci"
SKIPLIST_PATH = CI_DIR / "skiplist.yml"

# Where a run scatters its intermediate state. Overridable so a local operator
# can keep several runs side by side, and so a sharded CI job can hand each
# shard its own directory.
WORK_DIR = Path(os.environ.get("CI_WORK_DIR", REPO_ROOT / ".ci-work"))
RESULTS_DIR = Path(os.environ.get("CI_RESULTS_DIR", WORK_DIR / "results"))

# The name the runners import; the results directory holds the evidence.
EVIDENCE_DIR = RESULTS_DIR

# The binary under test. Every browser stage takes `--binary` and falls back to
# this; none of them has a default path, so a stage never tests a browser nobody
# chose.
BINARY_ENV = "CAMOUWOLF_BINARY"

# Test-only. A build that reads its config from another variable family than
# CAMOU_CONFIG_1..N is driven by copying every chunk under that prefix too
# (`KRAFT_CONFIG` for the Chromium 152 build the patch series was ported from).
# Unset in CI: a Camouwolf build reads CAMOU_CONFIG_*.
LEGACY_ENV_PREFIX_ENV = "CAMOUWOLF_CI_LEGACY_ENV_PREFIX"

# Salt for the HMACs that stand in for sundial's private vector names. A repo
# constant on purpose: inverting it still needs the private vector list.
DEFAULT_ID_SALT = "camouwolf-ci/v1"


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

_START = time.monotonic()


def log(msg: str, *, level: str = "INFO") -> None:
    # stderr, so a `--json` stdout stays machine-readable and every CLI here
    # composes with a pipe. Actions shows both streams either way.
    elapsed = time.monotonic() - _START
    print(f"[{elapsed:7.1f}s] {level:<5} {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    log(msg, level="FATAL")
    sys.exit(code)


def group(title: str) -> None:
    """Open a collapsible section in the Actions log (a no-op elsewhere)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::group::{title}", flush=True)
    else:
        log(f"--- {title} ---")


def set_output(name: str, value: str) -> None:
    """Publish a step output when running under Actions; echo otherwise."""
    log(f"output {name}={value}")
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    # Multi-line values need the heredoc form.
    with open(path, "a", encoding="utf-8") as fh:
        if "\n" in value:
            delim = f"__EOF_{hashlib.sha256(value.encode()).hexdigest()[:16]}__"
            fh.write(f"{name}<<{delim}\n{value}\n{delim}\n")
        else:
            fh.write(f"{name}={value}\n")


def summary(markdown: str) -> None:
    """Append to the Actions job summary (the human-facing proof surface)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        print(markdown, flush=True)
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(markdown.rstrip() + "\n")


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def combined(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")


def run(
    cmd: Sequence[str] | str,
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    check: bool = False,
    capture: bool = True,
    tee: bool = False,
    input: Optional[str] = None,
    kill_group: bool = False,
) -> Result:
    """Run a command. Never raises on a non-zero exit unless check=True.

    `kill_group` puts the command in its own process group and, on a timeout,
    kills the whole group: a test run that hangs has browsers under it, and
    killing only the runner would leave them behind. Output is not captured in
    that mode.
    """
    shell = isinstance(cmd, str)
    printable = cmd if shell else " ".join(shlex.quote(c) for c in cmd)
    log(f"$ {printable}" + (f"  (cwd={cwd})" if cwd else ""))

    full_env = {**os.environ, **(env or {})}
    if kill_group:
        import signal  # noqa: PLC0415

        child = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=full_env, shell=shell, start_new_session=True)
        try:
            return Result(child.wait(timeout=timeout), "", "")
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            log(f"TIMEOUT after {timeout}s; killed the process group of: {printable}", level="WARN")
            return Result(124, "", f"TIMEOUT after {timeout}s")
        except BaseException:
            # Ctrl-C reaches this process, not the new group: take the group down with it.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            raise
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=full_env,
            shell=shell,
            timeout=timeout,
            capture_output=capture and not tee,
            text=True,
            input=input,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        res = Result(124, out, err + f"\nTIMEOUT after {timeout}s")
        if check:
            die(f"command timed out: {printable}")
        return res

    res = Result(proc.returncode, proc.stdout or "", proc.stderr or "")
    if check and not res.ok:
        log(res.combined()[-4000:], level="ERROR")
        die(f"command failed ({res.code}): {printable}")
    return res


# --------------------------------------------------------------------------
# json / files
# --------------------------------------------------------------------------


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def gh_headers() -> Dict[str, str]:
    """GitHub API auth from the job's token, when there is one."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def http_json(url: str, *, timeout: int = 30, headers: Optional[Dict[str, str]] = None) -> Any:
    """GET a URL and parse JSON. Stdlib only so this works pre-pip."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "camouwolf-ci", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------


def opaque_id(raw: str, *, salt: Optional[str] = None) -> str:
    """Stable, non-invertible id for a private test vector.

    Used for anything sourced from sundial: a vector name in a committed file
    or a workflow log is a permanent leak, an HMAC is not.
    """
    key = (salt or os.environ.get("CI_ID_SALT") or DEFAULT_ID_SALT).encode()
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------

_VER_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(v: str) -> Tuple[int, int, int]:
    m = _VER_RE.match(v.strip())
    if not m:
        raise ValueError(f"unparseable version: {v!r}")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def major(v: str) -> int:
    return parse_version(v)[0]


# Stock Chrome's User-Agent; the group is the milestone. Chrome reduces the
# rest of the version to .0.0.0.
STOCK_UA = re.compile(
    r"^Mozilla/5\.0 \([^)]+\) AppleWebKit/537\.36 \(KHTML, like Gecko\) "
    r"Chrome/(\d+)\.0\.0\.0 Safari/537\.36$"
)


def binary_from(arg: Optional[Path]) -> Path:
    """The browser under test: `--binary`, else $CAMOUWOLF_BINARY. Never a default."""
    raw = str(arg) if arg else os.environ.get(BINARY_ENV, "").strip()
    if not raw:
        raise FileNotFoundError(
            f"no browser to test: pass --binary or set {BINARY_ENV}. A build tree's is "
            "<CAMOUWOLF_BUILD_DIR>/chromium/src/out/Camouwolf/chrome; a package's is "
            "camouwolf-<version>-<release>/camouwolf."
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no browser at {path}")
    return path.resolve()


def launcher_env() -> Dict[str, str]:
    """Environment for a child that imports the in-tree launcher."""
    src = str(REPO_ROOT / "python" / "src")
    return {"PYTHONPATH": os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH", "")]))}


def work_dir() -> Path:
    """WORK_DIR, created, and ignoring itself so nothing in it is ever staged."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    marker = WORK_DIR / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", encoding="utf-8")
    return WORK_DIR


def read_upstream_sh(path: Optional[Path] = None) -> Dict[str, str]:
    """Parse upstream.sh into a dict. It is shell, but strictly key=value."""
    path = path or (BROWSER_ROOT / "upstream.sh")
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("'\"")
    return out

