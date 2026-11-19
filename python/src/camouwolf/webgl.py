"""WebGL identities, from the Chrome devices fpgen has recorded.

Chrome reports its GPU through ANGLE, and the renderer string names the ANGLE
backend: Direct3D 11 on Windows, Metal (or OpenGL 4.1 on older Macs) on macOS,
desktop OpenGL or Vulkan on Linux. A renderer whose backend the claimed OS
never uses is a contradiction a page reads in one call, so every identity's
renderer is checked against its OS (backend_matches_os).

The browser answers getParameter() for the pnames in `webGl:parameters` before
its own lookup, for either context version and whether or not the extension a
pname belongs to is enabled (browser/patches/0007-webgl-spoofing.patch). So
only the limits both context versions report identically, and that need no
extension, are sent (webgl_parameters).
"""

import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Optional, Tuple

from .fingerprints import _FPGEN_OS, OS_KEYS, is_software_renderer
from camoucore.fpgen_model import load_fpgen

# The ANGLE backends Chrome uses on each OS, as they appear in the renderer.
_BACKENDS = {
    'win': re.compile(r'Direct3D11|D3D11', re.I),
    'mac': re.compile(r'Metal|OpenGL 4\.1', re.I),
    'lin': re.compile(r'OpenGL|Mesa|Vulkan', re.I),
}

Scalar = Any  # int | float | bool | str


def _os_key(target_os: str) -> str:
    target_os = target_os.lower()
    return OS_KEYS.get(target_os, target_os)


def backend_matches_os(renderer: Optional[str], target_os: str) -> bool:
    """Whether Chrome on `target_os` ('win', 'mac', 'lin', or the OS name) can
    report this renderer."""
    if not renderer:
        return True
    return bool(_BACKENDS[_os_key(target_os)].search(renderer))


def usable_gpu(gpu: Any, target_os: Optional[str] = None) -> bool:
    """Whether an identity may claim fpgen's `gpu` value: a real device, on a
    backend the OS uses."""
    if not isinstance(gpu, dict) or not gpu.get('renderer') or not gpu.get('vendor'):
        return False
    if is_software_renderer(gpu['renderer']):
        return False
    return target_os is None or backend_matches_os(gpu['renderer'], target_os)


def _comparable(entry: Any) -> Optional[Scalar]:
    """The scalar value of one recorded parameter, or None."""
    if not isinstance(entry, dict) or entry.get('type') != 'Number':
        return None
    value = entry.get('value')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def webgl_parameters(webgl: Any, webgl2: Any) -> Dict[str, Scalar]:
    """The `webGl:parameters` map of one recorded device: pname -> value.

    Kept: the numeric MAX_* limits of the WebGL1 core that the device's WebGL2
    context reports with the same value. Left to the real GPU: state (which a
    page changes and reads back), arrays (the key takes scalars), strings
    (VERSION and SHADING_LANGUAGE_VERSION differ between WebGL1 and WebGL2),
    extension limits (null until the extension is enabled) and anything the
    two context versions report differently.
    """
    params = webgl.get('params') if isinstance(webgl, dict) else None
    if not isinstance(params, dict):
        return {}
    params2 = webgl2.get('params') if isinstance(webgl2, dict) else None
    result: Dict[str, Scalar] = {}
    for pname, entry in params.items():
        value = _comparable(entry)
        name = entry.get('name', '') if isinstance(entry, dict) else ''
        if value is None or not name.startswith('MAX_') or name.endswith(('_EXT', '_WEBGL', '_OES')):
            continue
        if not str(pname).isdigit():
            continue
        if isinstance(params2, dict) and _comparable(params2.get(pname)) != value:
            continue
        result[str(pname)] = value
    return result


@lru_cache(maxsize=None)
def _trace_gpus(target_os: str) -> Tuple[Any, ...]:
    return tuple(load_fpgen().trace(target='gpu', browser='Chrome', os=_FPGEN_OS[target_os]))


def chrome_gpus(target_os: str) -> FrozenSet[Tuple[str, str]]:
    """Every (vendor, renderer) fpgen has seen Chrome report on this OS, that
    an identity may claim.

    A GPU outside this set has no recorded device behind it, so an identity
    naming it could only borrow another machine's limits, screen and fonts.
    """
    key = _os_key(target_os)
    return frozenset(
        (result.value['vendor'], result.value['renderer'])
        for result in _trace_gpus(key)
        if usable_gpu(result.value, key)
    )
