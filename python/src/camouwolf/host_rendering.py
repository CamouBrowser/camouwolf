"""What this machine's own GPU reports through the browser.

A library probe, not part of the launch: Camouwolf's identity names its GPU in
webGl:vendor / webGl:renderer, which the browser reports whatever the host
renders on, so a launch never needs to know the host's device. The probe
answers the question a caller choosing between headless modes has: does this
display render on hardware, or on SwiftShader?

    from camouwolf.host_rendering import host_gpu, renders_on_hardware
    gpu = host_gpu(executable_path, headless=True, env=os.environ, args=())
"""

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .fingerprints import is_software_renderer

Gpu = Tuple[str, str]

# What the launcher adds to the environment, which the probe leaves out: it
# reads the device, not an identity's claim about it.
_LAUNCHER_ENV = ('CAMOU_', 'CAMOUWOLF_UCRT_MATH', 'FONTCONFIG_FILE')

# A display that accepts TCP but never completes the X handshake leaves the
# browser's launch waiting forever; the probe is given this long.
HOST_GPU_PROBE_TIMEOUT_S = 30.0

_READ_GPU = """() => {
  const gl = document.createElement('canvas').getContext('webgl');
  const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
  if (!ext) return null;
  return [gl.getParameter(ext.UNMASKED_VENDOR_WEBGL), gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)];
}"""


def host_gpu(
    executable_path: str, headless: bool, env: Mapping[str, Any], args: Sequence[str] = ()
) -> Optional[Gpu]:
    """The (vendor, renderer) the browser reports on this machine with no
    identity applied, or None without WebGL.

    Read under the given display and switches: headless, Xvfb, a GPU display
    and a real display can each render on a different device, and so can a
    launch with gpudisplay.GPU_SWITCHES. The strings are ANGLE's.
    """
    return _host_gpu(
        str(executable_path),
        bool(headless),
        tuple(sorted((k, str(v)) for k, v in env.items() if not k.startswith(_LAUNCHER_ENV))),
        tuple(args),
    )


@lru_cache(maxsize=None)
def _host_gpu(
    executable_path: str,
    headless: bool,
    env: Tuple[Tuple[str, str], ...],
    args: Tuple[str, ...],
) -> Optional[Gpu]:
    # The probe runs in its own interpreter: the sync API refuses to start
    # inside a running asyncio loop, which is where AsyncCamouwolf calls
    # launch_options from, and a hung probe has to be stoppable, which a thread
    # is not. Terminating the child closes its pipe to the Playwright driver,
    # which then shuts the browser down. PYTHONPATH makes the child import this
    # same package.
    child_env = dict(os.environ)
    child_env['PYTHONPATH'] = os.pathsep.join(
        filter(None, (str(Path(__file__).resolve().parent.parent), child_env.get('PYTHONPATH')))
    )
    child = subprocess.Popen(
        [sys.executable, '-m', 'camouwolf.host_rendering'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        text=True,
    )
    request = json.dumps(
        {'executable_path': executable_path, 'headless': headless, 'env': dict(env), 'args': list(args)}
    )
    try:
        out, err = child.communicate(request, timeout=HOST_GPU_PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        child.terminate()
        child.communicate()
        display = dict(env).get('DISPLAY')
        context = f' (DISPLAY={display})' if display else ''
        raise TimeoutError(
            f'Host GPU probe timed out after {HOST_GPU_PROBE_TIMEOUT_S:g} s '
            f'while launching {executable_path}{context}. The browser did not '
            'answer the WebGL probe; check that the display accepts X connections.'
        ) from None
    if child.returncode != 0:
        raise RuntimeError(f'Host GPU probe failed while launching {executable_path}:\n{err.strip()}')
    gpu = json.loads(out)
    return (gpu[0], gpu[1]) if gpu else None


def _read_host_gpu(
    executable_path: str,
    headless: bool,
    env: Dict[str, str],
    args: List[str],
) -> Optional[List[str]]:
    """Launch the binary once and read its WebGL vendor and renderer."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable_path,
            headless=headless,
            env=env,
            args=args,
        )
        try:
            return browser.new_page().evaluate(_READ_GPU)
        finally:
            browser.close()


def renders_on_hardware(gpu: Optional[Gpu]) -> bool:
    """Whether the host's canvas and WebGL come from a GPU, not a software rasterizer."""
    return gpu is not None and not is_software_renderer(gpu[1])


if __name__ == '__main__':
    # _host_gpu's child: reads its request from stdin, writes the GPU as JSON.
    request = json.load(sys.stdin)
    json.dump(_read_host_gpu(**request), sys.stdout)
