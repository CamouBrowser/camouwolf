"""Chrome identities: drawing one with fpgen and turning it into Camouwolf config.

An fpgen fingerprint is one recorded Chrome on one real machine, so its fields
agree with each other; the launcher maps them and leaves them together. What
the record cannot know is filled in afterwards: the Chromium milestone the
binary really is, and the client hints that milestone and OS imply
(camouwolf.clienthints).
"""

import math
import os
import struct
from dataclasses import dataclass
from random import randrange
from typing import Any, Callable, Dict, List, Optional, Tuple

import orjson

from camouwolf.clienthints import retarget_user_agent
from camouwolf.pkgman import LAUNCHER_CONSTANTS, LOCAL_DATA, load_yaml

# Load the fpgen mapping file
FPGEN_DATA = load_yaml('fpgen.yml')

# Camouwolf's OS names and the keys the rest of the launcher uses for them.
OS_KEYS = {'macos': 'mac', 'windows': 'win', 'linux': 'lin'}
OS_NAMES = {key: name for name, key in OS_KEYS.items()}


def platform_to_os(platform: str) -> str:
    """The OS name ('macos', 'windows', 'linux') a navigator.platform belongs to."""
    if platform.startswith('Mac'):
        return 'macos'
    if platform.startswith('Win'):
        return 'windows'
    return 'linux'



# fpgen's OS names, from Camouwolf's.
_FPGEN_OS = {'lin': 'Linux', 'linux': 'Linux', 'mac': 'macOS', 'macos': 'macOS',
             'win': 'Windows', 'windows': 'Windows'}

# fpgen unpacks ~7 MB of model data on first use, so the generator is built on
# demand rather than at import: `import camouwolf` must stay cheap for callers
# who never generate a fingerprint (they passed their own, or a preset).
_FP_GENERATOR = None


def _generator():
    global _FP_GENERATOR
    if _FP_GENERATOR is None:
        from camoucore.fpgen_model import load_fpgen

        _FP_GENERATOR = load_fpgen().Generator()
    return _FP_GENERATOR


@dataclass
class Screen:
    """A bound on the screen a generated fingerprint may claim: the generated
    screen must fit inside the given fields."""

    min_width: Optional[int] = None
    max_width: Optional[int] = None
    min_height: Optional[int] = None
    max_height: Optional[int] = None

    def as_conditions(self) -> Dict[str, Any]:
        """The bound, as fpgen conditions.

        fpgen takes a predicate per field and rejects a value the predicate
        refuses, so a range is a closure rather than a list of allowed values.
        """
        conditions: Dict[str, Any] = {}
        lo_w, hi_w = self.min_width, self.max_width
        lo_h, hi_h = self.min_height, self.max_height
        if lo_w is not None or hi_w is not None:
            conditions['screen.width'] = lambda w: (
                isinstance(w, int) and (lo_w is None or w >= lo_w) and (hi_w is None or w <= hi_w)
            )
        if lo_h is not None or hi_h is not None:
            conditions['screen.height'] = lambda h: (
                isinstance(h, int) and (lo_h is None or h >= lo_h) and (hi_h is None or h <= hi_h)
            )
        return conditions


def host_cpu_count() -> Optional[int]:
    """Logical CPUs this process may actually run on (cgroup/affinity aware)."""
    try:
        return len(os.sched_getaffinity(0)) or None  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count()


# Core counts real desktop machines ship with, from the recorded presets. 2 is
# excluded although the scraped corpus has it: no Apple Silicon part has fewer
# than 8 cores, so 2 beside the common "Apple M1" renderer is a machine that
# does not exist. A host outside the table is snapped into it.
PLAUSIBLE_CORE_COUNTS = tuple(LAUNCHER_CONSTANTS['plausibleCoreCounts'])


def fix_hardware_concurrency(config: Dict[str, Any], can_pin: Optional[bool] = None) -> None:
    """navigator.hardwareConcurrency = the host's parallelism, snapped DOWN
    into PLAUSIBLE_CORE_COUNTS.

    A drawn value that differs from the machine the browser runs on is
    measurable from a page: timing N parallel workers reveals how many cores
    are really usable. So the drawn value is kept only when the browser can be
    pinned to that many cores (cpu_affinity: Linux, Windows, with
    pin_cpu_cores); otherwise the host's own snapped count is reported. A
    caller that sets navigator.hardwareConcurrency themselves is left alone.
    """
    n = host_cpu_count()
    if not n:
        return
    # The fingerprint's own value survives when the browser can be pinned to
    # that many cores (cpu_affinity: Linux, Windows): reported and measurable
    # then agree by construction, and the identity keeps its diversity. A draw
    # the host cannot honour (more cores than it has), a host that cannot pin
    # (macOS), or pin_cpu_cores left off -- the default since 2026-09-17 --
    # falls back to the snapped host count, which is equally coherent.
    from camoucore.cpu_affinity import supported as _can_pin

    # can_pin=False: the caller launches the browser itself and nothing will
    # pin it (launch_server, launch_options used directly), so a kept draw
    # would be measured as the host count. None: whatever the host supports.
    pinnable = _can_pin() and can_pin is not False

    cap = int(n)
    host_allowed = [c for c in PLAUSIBLE_CORE_COUNTS if c <= cap]
    host_value = host_allowed[-1] if host_allowed else PLAUSIBLE_CORE_COUNTS[0]

    drawn = config.get('navigator.hardwareConcurrency')
    if pinnable and isinstance(drawn, int) and drawn >= 1:
        # The draw is kept for diversity, snapped down into the table and capped
        # by the host so pinning can honour it.
        target = min(drawn, cap)
        allowed = [c for c in PLAUSIBLE_CORE_COUNTS if c <= target]
        # The floor is the table's even on a 1-3 core host: reporting the host's
        # own 1, 2 or 3 would contradict the drawn GPU (see the table above).
        config['navigator.hardwareConcurrency'] = (
            allowed[-1] if allowed else PLAUSIBLE_CORE_COUNTS[0]
        )
        return
    config['navigator.hardwareConcurrency'] = host_value



def clamp_window_dimensions(config: Dict[str, Any]) -> None:
    """Enforce inner <= outer <= avail <= screen on BOTH axes.

    The browser faithfully reports whatever we inject, so a generated
    fingerprint that ships e.g. outerWidth > screen.width or innerWidth >
    outerWidth leaks as an impossible geometry. Shrink each level down to its
    container, preserving the chrome delta between outer and inner where
    possible.
    """
    for axis in ('Width', 'Height'):
        screen = config.get(f'screen.{axis.lower()}')
        avail = config.get(f'screen.avail{axis}')
        outer = config.get(f'window.outer{axis}')
        inner = config.get(f'window.inner{axis}')

        # avail must not exceed screen
        if screen and avail and avail > screen:
            config[f'screen.avail{axis}'] = screen
        avail_clamped = config.get(f'screen.avail{axis}', screen)

        # outer must not exceed avail (or screen if avail is unknown)
        outer_cap = avail_clamped if avail_clamped is not None else screen
        if outer and outer_cap and outer > outer_cap:
            chrome = max(0, outer - inner) if inner else 0
            config[f'window.outer{axis}'] = outer_cap
            if inner:
                config[f'window.inner{axis}'] = max(1, outer_cap - chrome)

        # inner must not exceed outer
        outer_clamped = config.get(f'window.outer{axis}', outer)
        inner_now = config.get(f'window.inner{axis}')
        if inner_now and outer_clamped and inner_now > outer_clamped:
            config[f'window.inner{axis}'] = outer_clamped


def clamp_screen_to_display(
    config: Dict[str, Any],
    max_width: Optional[int],
    max_height: Optional[int],
) -> None:
    """Shrink screen.width/height down to the bounds of the real display.

    The Screen bound passed to generate_fingerprint() is dropped when the model
    has nothing that fits it, and a preset or a caller's fingerprint is not
    bounded at all. The launcher sizes the real window to window.outerWidth/outerHeight, so an
    unbounded value renders past the edge of the monitor.

    Keeps the taskbar delta (screen - avail) intact. Callers must run
    clamp_window_dimensions afterwards to cascade the new bounds down to
    avail/outer/inner.
    """
    for axis, cap in (('width', max_width), ('height', max_height)):
        screen = config.get(f'screen.{axis}')
        if not (screen and cap) or screen <= cap:
            continue
        avail_key = 'screen.availWidth' if axis == 'width' else 'screen.availHeight'
        avail = config.get(avail_key)
        config[f'screen.{axis}'] = cap
        if avail:
            config[avail_key] = max(1, cap - max(0, screen - avail))


def clamp_window_position(config: Dict[str, Any]) -> None:
    """Keep the window box inside the screen: 0 <= screenX/Y <= screen - outer.

    A generated screenX/screenY is consistent with the screen it was generated
    against, so clamp_screen_to_display invalidates it. A window
    positioned partly off its own reported screen is an impossible geometry.
    """
    for axis, pos_key in (('Width', 'window.screenX'), ('Height', 'window.screenY')):
        screen = config.get(f'screen.{axis.lower()}')
        outer = config.get(f'window.outer{axis}')
        pos = config.get(pos_key)
        if pos is None or not (screen and outer):
            continue
        config[pos_key] = max(0, min(pos, screen - outer))




def _f32(value: float) -> float:
    return struct.unpack('f', struct.pack('f', value))[0]


def chrome_window_reports(position: int, size: int, dpr: float) -> Tuple[int, int]:
    """The window position and size, in CSS pixels, Chrome reads back on X11
    for a window it was asked to open at `position` and `size`.

    The bounds go to device pixels as ToPixelRect (float32, enclosing rect, a
    0.001 rounding allowance) and come back as ToDIPRect (float32 times
    1.f/scale, enclosing rect, no allowance), so at 1.75 a 1000 px window is
    reported 1001 px wide.
    """
    scale = _f32(dpr)

    def outward(value: float, rounding: Callable[[float], int]) -> int:
        nearest = round(value)
        return nearest if abs(nearest - value) < _f32(0.001) else rounding(value)

    left = _f32(_f32(position) * scale)
    right = _f32(left + _f32(_f32(size) * scale))
    left_px, right_px = outward(left, math.floor), outward(right, math.ceil)
    inverse = _f32(1 / scale)
    back = _f32(_f32(left_px) * inverse)
    back_right = _f32(back + _f32(_f32(right_px - left_px) * inverse))
    return math.floor(back), math.ceil(back_right) - math.floor(back)


def _window_inputs(position: int, size: int, dpr: float) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """(switch input, what Chrome reports for it) around `position`, `size`."""
    return [
        ((p, z), chrome_window_reports(p, z, dpr))
        for p in range(max(0, position - 8), position + 3)
        for z in range(max(1, size - 16), size + 5)
    ]


def snap_window_to_device_pixels(config: Dict[str, Any]) -> None:
    """Claim the nearest window Chrome can report at its devicePixelRatio.

    At a fractional ratio some positions and sizes are never read back (at 1.75
    no window reports screenX 7). The claim moves to the nearest one Chrome
    does report, inside the drawn window's box so it still fits the screen.
    """
    dpr = float(config.get('window.devicePixelRatio') or 1)
    for axis, pos_key in (('Width', 'window.screenX'), ('Height', 'window.screenY')):
        size_key = f'window.outer{axis}'
        size, position = config.get(size_key), config.get(pos_key) or 0
        if not size:
            continue
        reachable = [
            seen for _, seen in _window_inputs(position, size, dpr)
            if position >= seen[0] >= 0 and seen[0] + seen[1] <= position + size
        ]
        if reachable:
            seen_position, config[size_key] = min(
                reachable, key=lambda seen: abs(position - seen[0]) + abs(size - seen[1])
            )
            if pos_key in config:
                config[pos_key] = seen_position


def window_switch_bounds(position: int, size: int, dpr: float) -> Tuple[int, int]:
    """The --window-position/--window-size values Chrome reports back as
    `position`, `size`; the claim itself when no input near it does."""
    for given, seen in _window_inputs(position, size, dpr):
        if seen == (position, size):
            return given
    return position, size


MODERN_SCREEN_FLOOR: Tuple[int, int] = (1366, 768)


def raise_screen_to_modern_floor(config: Dict[str, Any]) -> None:
    """Lift netbook-era screen geometry to something current hardware reports.

    Panels like 1024x600 left production a decade and a half ago, so the
    screen is what has to move: no GPU choice makes that profile look current.

    Keeps the screen-to-avail gap intact; the window box is reconciled by
    clamp_window_dimensions and clamp_window_position, which run after this.
    Call BEFORE clamp_screen_to_display so a genuinely small real monitor
    still wins.
    """
    min_w, min_h = MODERN_SCREEN_FLOOR
    sw = config.get('screen.width')
    sh = config.get('screen.height')
    if not (sw and sh) or (sw >= min_w and sh >= min_h):
        return

    # Measure the gaps before mutating, or they get folded into themselves.
    aw = config.get('screen.availWidth')
    ah = config.get('screen.availHeight')
    gap_w = sw - aw if aw else None
    gap_h = sh - ah if ah else None

    new_w, new_h = max(sw, min_w), max(sh, min_h)
    config['screen.width'] = new_w
    config['screen.height'] = new_h
    if gap_w is not None:
        config['screen.availWidth'] = max(1, new_w - max(0, gap_w))
    if gap_h is not None:
        config['screen.availHeight'] = max(1, new_h - max(0, gap_h))



_SOFTWARE_RENDERERS: Tuple[str, ...] = (
    'llvmpipe',
    'Microsoft Basic Render Driver',
    'SwiftShader',
    'Generic Renderer',
)


def is_software_renderer(renderer: Optional[str]) -> bool:
    """Whether `renderer` is a software rasterizer rather than real hardware.

    Case-insensitive: fpgen hands a condition's predicate the value lowercased.
    """
    if not renderer:
        return False
    lowered = renderer.lower()
    return any(name.lower() in lowered for name in _SOFTWARE_RENDERERS)


# The taskbar, menu bar or panel a desktop reserves, in CSS pixels. A screen
# whose available area equals its whole area on both axes has none, which only
# a kiosk shows.
TASKBAR_HEIGHT: Dict[str, int] = LAUNCHER_CONSTANTS['taskbarHeight']


def fix_screen_no_taskbar(config: Dict[str, Any], target_os: str) -> None:
    """Reserve the OS's taskbar when the identity's available area is its whole screen."""
    width, height = config.get('screen.width'), config.get('screen.height')
    if not (width and height):
        return
    if config.get('screen.availWidth') != width or config.get('screen.availHeight') != height:
        return
    taskbar = TASKBAR_HEIGHT[target_os]
    config['screen.availHeight'] = height - taskbar
    if target_os == 'mac':
        config['screen.availTop'] = taskbar
    outer = config.get('window.outerHeight')
    if outer and outer > height - taskbar:
        config['window.outerHeight'] = height - taskbar


# Families every installation of the OS has. fpgen records the fonts a probe
# list found, so a short record (six families on some Linux machines) would
# otherwise leave a page with almost nothing to render in.
ESSENTIAL_FONTS: Dict[str, List[str]] = LAUNCHER_CONSTANTS['essentialFonts']

# The families each OS's persona can render from the shipped font bundle
# (scripts/gen-bundle-fonts.py). A claimed family the bundle cannot render is
# caught by one measureText call, so nothing outside this list is claimed.
BUNDLE_FONTS: Dict[str, frozenset] = {
    os_key: frozenset(f.lower() for f in families)
    for os_key, families in orjson.loads((LOCAL_DATA / 'data' / 'bundle-fonts.json').read_bytes())['families'].items()
}

# The devices a machine with nothing plugged in enumerates, before the page has
# media permission: one entry per kind, with empty id, label and group.
MEDIA_DEVICE_KINDS: Tuple[str, ...] = ('audioinput', 'videoinput', 'audiooutput')
DEFAULT_MEDIA_DEVICES: Dict[str, List[str]] = {
    'win': ['audioinput|||', 'videoinput|||', 'audiooutput|||'],
    'mac': ['audioinput|||', 'videoinput|||', 'audiooutput|||'],
    'lin': ['audioinput|||', 'audiooutput|||'],
}


def media_devices(devices: Any) -> Optional[List[str]]:
    """fpgen's device counts as `mediaDevices:enumerated` entries.

    Before a page holds media permission Chrome lists at most one device of
    each kind, with empty deviceId, label and groupId.
    """
    if not isinstance(devices, dict):
        return None
    listed = [f'{kind}|||' for kind in MEDIA_DEVICE_KINDS if (devices.get(kind) or 0) > 0]
    return listed or None


def pack_voices(voices: Any) -> Optional[List[str]]:
    """fpgen's speechSynthesis voices as `voices` entries: name|lang|uri|local|default."""
    if not isinstance(voices, list):
        return None
    packed = []
    for voice in voices:
        if not isinstance(voice, dict) or not voice.get('name') or not voice.get('lang'):
            continue
        fields = (voice['name'], voice['lang'], voice.get('voiceURI') or voice['name'])
        if any('|' in str(field) for field in fields):
            continue  # the separator; no recorded voice has one
        packed.append(
            '|'.join(
                [*map(str, fields), '1' if voice.get('localService') else '0', '1' if voice.get('default') else '0']
            )
        )
    return packed or None


def webgpu_adapter(gpu_info: Any) -> Dict[str, str]:
    """The WebGPU adapter info of an fpgen record, as webGpu:* keys.

    A record whose machine had no WebGPU adapter carries an empty list, and
    nothing is claimed for it.
    """
    adapter = gpu_info.get('adapter') if isinstance(gpu_info, dict) else None
    default = adapter.get('default') if isinstance(adapter, dict) else None
    info = default.get('info') if isinstance(default, dict) else None
    if not isinstance(info, dict):
        return {}
    return {
        f'webGpu:{key}': info[key]
        for key in ('vendor', 'architecture', 'device')
        if isinstance(info.get(key), str)
    }


def font_list(fonts: Any, target_os: str) -> Optional[List[str]]:
    """The identity's installed families: the record's, with the OS's own,
    less any the bundle cannot render for that OS."""
    if not isinstance(fonts, list) or not fonts:
        return None
    shippable = BUNDLE_FONTS[target_os]
    families: Dict[str, str] = {}
    for name in [*fonts, *ESSENTIAL_FONTS[target_os]]:
        if isinstance(name, str) and name.lower() in shippable:
            families.setdefault(name.lower(), name)
    return list(families.values())


def _cast_to_properties(
    camouwolf_data: Dict[str, Any],
    cast_enum: Dict[str, Any],
    fp: Dict[str, Any],
) -> None:
    """Copy the scalar fields fpgen.yml names into Camouwolf config properties."""
    for key, data in fp.items():
        type_key = cast_enum.get(key)
        if not type_key:
            continue
        if isinstance(data, dict):
            if isinstance(type_key, dict):
                _cast_to_properties(camouwolf_data, type_key, data)
            continue
        # fpgen writes a field the browser did not have as None or "undefined".
        if data is None or data == '' or data == 'undefined' or isinstance(data, list):
            continue
        if not isinstance(type_key, str):
            continue
        # Fix values that are out of bounds
        if type_key.startswith('screen.') and isinstance(data, int) and data < 0:
            data = 0
        camouwolf_data[type_key] = data


def handle_screenXY(camouwolf_data: Dict[str, Any], fingerprint: Dict[str, Any]) -> None:
    """
    Helper method to set window.screenY based on the generated screenX value.
    """
    # Skip if manually provided
    if 'window.screenY' in camouwolf_data:
        return
    screen = fingerprint.get('screen') or {}
    window = fingerprint.get('window') or {}
    # Default screenX to 0 if not provided
    screenX = window.get('screenX')
    if not screenX:
        camouwolf_data['window.screenX'] = 0
        camouwolf_data['window.screenY'] = 0
        return

    # If screenX is within [-50, 50], use the same value for screenY
    if screenX in range(-50, 51):
        camouwolf_data['window.screenY'] = screenX
        return

    # The generator thinks the browser is windowed. Randomly generate a screenY.
    screenY = (screen.get('availHeight') or 0) - (window.get('outerHeight') or 0)
    if screenY == 0:
        camouwolf_data['window.screenY'] = 0
    elif screenY > 0:
        camouwolf_data['window.screenY'] = randrange(0, screenY)  # nosec
    else:
        camouwolf_data['window.screenY'] = randrange(screenY, 0)  # nosec



def fingerprint_os(fingerprint: Dict[str, Any]) -> Optional[str]:
    """The OS key ('win', 'mac', 'lin') an fpgen fingerprint was recorded on."""
    from camouwolf.clienthints import ua_os

    user_agent = (fingerprint.get('navigator') or {}).get('userAgent')
    return ua_os(user_agent) if isinstance(user_agent, str) else None


def from_fpgen(fingerprint: Dict[str, Any], chrome_version: Optional[Any] = None) -> Dict[str, Any]:
    """
    Converts an fpgen Chrome fingerprint to a Camouwolf config.

    `chrome_version` is the Chromium milestone of the browser that will run; the
    recorded user agent is rewritten to it.
    """
    from camouwolf.webgl import webgl_parameters

    camouwolf_data: Dict[str, Any] = {}
    _cast_to_properties(camouwolf_data, FPGEN_DATA, fingerprint)
    handle_screenXY(camouwolf_data, fingerprint)

    user_agent = camouwolf_data.get('navigator.userAgent')
    if chrome_version and isinstance(user_agent, str):
        camouwolf_data['navigator.userAgent'] = retarget_user_agent(
            user_agent, str(chrome_version).split('.', 1)[0]
        )
    target_os = fingerprint_os(fingerprint)

    voices = pack_voices(fingerprint.get('voices'))
    if voices:
        camouwolf_data['voices'] = voices
    parameters = webgl_parameters(fingerprint.get('webgl'), fingerprint.get('webgl2'))
    if parameters and camouwolf_data.get('webGl:renderer'):
        camouwolf_data['webGl:parameters'] = parameters
    camouwolf_data.update(webgpu_adapter(fingerprint.get('gpuInfo')))
    devices = media_devices(fingerprint.get('devices'))
    if devices:
        camouwolf_data['mediaDevices:enumerated'] = devices
    if target_os:
        fonts = font_list(fingerprint.get('allFonts'), target_os)
        if fonts:
            camouwolf_data['fonts'] = fonts

    return camouwolf_data


def handle_window_size(fp: Dict[str, Any], outer_width: int, outer_height: int) -> None:
    """
    Helper method to set a custom outer window size, and center it in the screen
    """
    screen = fp.setdefault('screen', {})
    window = fp.setdefault('window', {})

    # Center the window on the screen
    window['screenX'] = (window.get('screenX') or 0) + (
        (screen.get('width') or outer_width) - outer_width
    ) // 2
    window['screenY'] = ((screen.get('height') or outer_height) - outer_height) // 2

    # Update inner dimensions if set
    if window.get('innerWidth'):
        window['innerWidth'] = max(
            outer_width - (window.get('outerWidth') or 0) + window['innerWidth'], 0
        )
    if window.get('innerHeight'):
        window['innerHeight'] = max(
            outer_height - (window.get('outerHeight') or 0) + window['innerHeight'], 0
        )

    # Set outer dimensions
    window['outerWidth'] = outer_width
    window['outerHeight'] = outer_height



def generate_fingerprint(
    window: Optional[Tuple[int, int]] = None,
    screen: Optional[Screen] = None,
    os: Optional[Any] = None,
    **conditions: Any,
) -> Dict[str, Any]:
    """
    Generates a Chrome fingerprint with fpgen.

    `screen` bounds the generated screen; `window` overrides the outer window
    size afterwards. `os` is Camouwolf's name for the platform ('linux', 'macos',
    'windows', or several); anything else is passed to fpgen as a condition.
    """
    if os:
        names = [os] if isinstance(os, str) else list(os)
        try:
            resolved = [_FPGEN_OS[str(n).lower()] for n in names]
        except KeyError as exc:
            raise ValueError(f'Unknown OS for fingerprint generation: {exc.args[0]!r}') from None
        # fpgen takes one value, or a tuple of alternatives (a list is read as
        # one value).
        conditions['os'] = resolved[0] if len(resolved) == 1 else tuple(dict.fromkeys(resolved))
    screen_conditions = screen.as_conditions() if screen is not None else {}
    try:
        fingerprint = _generator().generate(browser='Chrome', **conditions, **screen_conditions)
    except Exception as exc:  # fpgen.exceptions.InvalidConstraints
        if not screen_conditions or type(exc).__name__ != 'InvalidConstraints':
            raise
        # The screen bound is best-effort: a display the model has nothing to
        # fit (a 1x1 Xvfb, a small panel) must not stop generation, and
        # clamp_screen_to_display() still bounds the result afterwards.
        fingerprint = _generator().generate(browser='Chrome', **conditions)

    if window:  # User-specified outer window size
        handle_window_size(fingerprint, *window)
    return fingerprint


if __name__ == "__main__":
    from pprint import pprint

    pprint(from_fpgen(generate_fingerprint()))
