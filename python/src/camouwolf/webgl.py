"""WebGL identities, from the Chrome devices fpgen has recorded.

Chrome reports its GPU through ANGLE, and the renderer string names the ANGLE
backend: Direct3D 11 on Windows, Metal (or OpenGL 4.1 on older Macs) on macOS,
desktop OpenGL or Vulkan on Linux. A renderer whose backend the claimed OS
never uses is a contradiction a page reads in one call, so every identity's
renderer is checked against its OS (backend_matches_os).

A device's WebGL identity is more than its renderer string: the limits, the
shader precision table and the extension lists are the backend's signature
(Direct3D 11 reports ALIASED_LINE_WIDTH_RANGE [1, 1] and MAX_VIEWPORT_DIMS
[32767, 32767] whatever the card), so they are claimed from the same record
(webgl_identity). The browser answers them before its own lookup
(browser/patches/0007-webgl-spoofing.patch); an extension it lacks cannot be
added, only one the claimed device lacks hidden.
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


# The GL spec's implementation-dependent values: fixed per device, never state a
# page sets. Extension limits are left out, because the browser answers a
# configured pname whether or not its extension is enabled.
_LIMIT_PREFIXES = ('MAX_', 'MIN_', 'ALIASED_')
_LIMITS = frozenset({'SUBPIXEL_BITS', 'UNIFORM_BUFFER_OFFSET_ALIGNMENT'})
_EXTENSION_SUFFIXES = ('_EXT', '_WEBGL', '_OES')


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _limit(entry: Any) -> Optional[Any]:
    """The value of one recorded parameter if it is a device limit, or None."""
    if not isinstance(entry, dict):
        return None
    name = entry.get('name') or ''
    if name.endswith(_EXTENSION_SUFFIXES) or not (name.startswith(_LIMIT_PREFIXES) or name in _LIMITS):
        return None
    value = entry.get('value')
    if entry.get('type') == 'Number' and _number(value):
        return value
    if entry.get('type') in ('Float32Array', 'Int32Array') and isinstance(value, list) \
            and 2 <= len(value) <= 4 and all(map(_number, value)):
        return list(value)
    return None


def webgl_parameters(context: Any) -> Dict[str, Any]:
    """The device limits one recorded context reports: pname -> value."""
    params = context.get('params') if isinstance(context, dict) else None
    if not isinstance(params, dict):
        return {}
    return {
        str(pname): value
        for pname, value in ((pname, _limit(entry)) for pname, entry in params.items())
        if value is not None and str(pname).isdigit()
    }


def shader_precision_formats(context: Any) -> Dict[str, Dict[str, int]]:
    """A recorded getShaderPrecisionFormat table, keyed "<shaderType>,<precisionType>"."""
    formats = context.get('shaderPrecisionFormats') if isinstance(context, dict) else None
    table: Dict[str, Dict[str, int]] = {}
    for item in formats if isinstance(formats, list) else []:
        spf = item.get('shaderPrecisionFormat') if isinstance(item, dict) else None
        if isinstance(spf, dict) and all(isinstance(spf.get(k), int) for k in ('rangeMin', 'rangeMax', 'precision')):
            table[f"{item['shaderType']},{item['precisionType']}"] = {
                k: spf[k] for k in ('rangeMin', 'rangeMax', 'precision')
            }
    return table


def readout_names(fingerprint: Dict[str, Any], renderer: Optional[str]) -> bool:
    """Whether the record's WebGL1 and WebGL2 readouts are both of `renderer`.

    fpgen occasionally pairs one device's GPU with another's readout, or with
    none; an identity drawn from such a record has no limits for its renderer.
    """
    return bool(renderer) and all(
        isinstance(r, dict) and r.get('renderer') == renderer
        for r in (fingerprint.get('webgl'), fingerprint.get('webgl2'))
    )


def webgl_identity(fingerprint: Dict[str, Any], renderer: Optional[str]) -> Dict[str, Any]:
    """The webGl:* and webGl2:* keys of the record's WebGL1 and WebGL2 contexts,
    claimed only from a readout of `renderer` (readout_names)."""
    if not readout_names(fingerprint, renderer):
        return {}
    webgl, webgl2 = fingerprint['webgl'], fingerprint['webgl2']
    claimed = {
        'webGl:parameters': webgl_parameters(webgl),
        'webGl2:parameters': webgl_parameters(webgl2),
        'webGl:shaderPrecisionFormats': shader_precision_formats(webgl),
        'webGl2:shaderPrecisionFormats': shader_precision_formats(webgl2),
        'webGl:supportedExtensions': webgl.get('supportedExtensions'),
        'webGl2:supportedExtensions': webgl2.get('supportedExtensions'),
    }
    return {key: value for key, value in claimed.items() if value and isinstance(value, (dict, list))}


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
