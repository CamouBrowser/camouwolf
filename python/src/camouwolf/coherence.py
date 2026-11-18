"""Whole-identity coherence: the checks that look at more than one field.

An identity is assembled from several sources: an fpgen record, the client
hints derived for the binary's milestone, the caller's own `config`, and the
host (its core count, its display). A combination no machine has can be built
from individually plausible parts: a Windows user agent beside a macOS
Sec-CH-UA-Platform, a Direct3D renderer on a Linux identity, an Apple GPU
behind an x86 Sec-CH-UA-Arch.

Every identity passes through here on every launch, whatever it was built
from. Each rule states an invariant that holds on real Chrome, says how to
repair an identity that breaks it where a correct value is determined, and is
checked again afterwards. `validate()` reports what is still broken; `apply()`
repairs what it can.

Camoufox's rules for Firefox-only surfaces (navigator.oscpu, cpu:arch and
cpu:manufacturer, Firefox's sanitized renderer buckets, the 86px chrome
height) have no Chrome counterpart and are not here.
"""

from typing import AbstractSet, Any, Callable, Dict, List, NamedTuple, Optional, Tuple

from . import clienthints
from .webgl import backend_matches_os

# screen.colorDepth: Chrome reports 24, or 30 on a deep-colour display (most
# Macs). 32 appears in scraped data and is not a value Chrome emits.
PLAUSIBLE_COLOR_DEPTH = frozenset({24, 30})

# navigator.deviceMemory is bucketed: a power of two, floored at 0.25 and
# capped (at 8 until Chrome 140, at 32 on desktop since).
PLAUSIBLE_DEVICE_MEMORY: Tuple[float, ...] = (0.25, 0.5, 1, 2, 4, 8, 16, 32)

# navigator.hardwareConcurrency: at least one logical core, and no consumer
# machine has more than this.
MAX_PLAUSIBLE_CORES = 256


class Violation(NamedTuple):
    rule: str
    detail: str
    #: The caller's own keys that the repair would have changed, and which were
    #: kept as passed instead. Empty when nothing of the caller's is involved.
    kept: Tuple[str, ...] = ()


class Rule(NamedTuple):
    name: str
    #: Returns a description of the breakage, or None when the identity holds.
    check: Callable[[Dict[str, Any], str], Optional[str]]
    #: Repairs the identity in place. None where no correct value is determined.
    repair: Optional[Callable[[Dict[str, Any], str], None]]
    #: Keys the repair only adjusts to stay consistent with what it repairs. A
    #: caller who set any of them keeps all of them as they were, the repair
    #: itself still runs, and they never block it.
    adjusts: Tuple[str, ...] = ()


def _renderer(config: Dict[str, Any]) -> str:
    return str(config.get('webGl:renderer') or '')


def _check_client_hints(config: Dict[str, Any], target_os: str) -> Optional[str]:
    problems = clienthints.assert_coherent(config)
    return ' '.join(problems) if problems else None


def _check_target_os(config: Dict[str, Any], target_os: str) -> Optional[str]:
    platform = config.get('navigator.platform')
    if platform and platform != clienthints.NAV_PLATFORM[target_os]:
        return (
            f'navigator.platform is "{platform}" on a {clienthints.CH_PLATFORM[target_os]} '
            f'identity (expected "{clienthints.NAV_PLATFORM[target_os]}")'
        )
    return None


def _check_gpu_matches_os(config: Dict[str, Any], target_os: str) -> Optional[str]:
    renderer = _renderer(config)
    if renderer and not backend_matches_os(renderer, target_os):
        return (
            f'WebGL renderer "{renderer}" is not on an ANGLE backend Chrome uses on '
            f'{clienthints.CH_PLATFORM[target_os]}'
        )
    return None


def _apple_gpu(config: Dict[str, Any]) -> bool:
    return 'Apple M' in _renderer(config)


def _check_mac_architecture(config: Dict[str, Any], target_os: str) -> Optional[str]:
    arch = config.get('userAgentData.architecture')
    renderer = _renderer(config)
    if target_os != 'mac' or not arch or not renderer:
        return None
    expected = 'arm' if _apple_gpu(config) else 'x86'
    if arch != expected:
        return (
            f'Sec-CH-UA-Arch is "{arch}" beside the WebGL renderer "{renderer}"; '
            f'that GPU is in {"Apple Silicon" if expected == "arm" else "Intel"} Macs ("{expected}")'
        )
    return None


def _repair_mac_architecture(config: Dict[str, Any], target_os: str) -> None:
    config['userAgentData.architecture'] = 'arm' if _apple_gpu(config) else 'x86'


def _check_color_depth(config: Dict[str, Any], target_os: str) -> Optional[str]:
    depth = config.get('screen.colorDepth')
    if depth is not None and depth not in PLAUSIBLE_COLOR_DEPTH:
        return f'screen.colorDepth {depth} is not a value Chrome reports (24 or 30)'
    return None


def _repair_color_depth(config: Dict[str, Any], target_os: str) -> None:
    config['screen.colorDepth'] = 24


def _check_device_memory(config: Dict[str, Any], target_os: str) -> Optional[str]:
    memory = config.get('navigator.deviceMemory')
    if memory is not None and memory not in PLAUSIBLE_DEVICE_MEMORY:
        return f'navigator.deviceMemory {memory} is not one of the buckets Chrome reports'
    return None


def _repair_device_memory(config: Dict[str, Any], target_os: str) -> None:
    memory = config['navigator.deviceMemory']
    below = [bucket for bucket in PLAUSIBLE_DEVICE_MEMORY if bucket <= memory]
    bucket = below[-1] if below else PLAUSIBLE_DEVICE_MEMORY[0]
    config['navigator.deviceMemory'] = int(bucket) if bucket >= 1 else bucket


def _check_cores(config: Dict[str, Any], target_os: str) -> Optional[str]:
    cores = config.get('navigator.hardwareConcurrency')
    if cores is None:
        return None
    if not isinstance(cores, int) or isinstance(cores, bool) or not 1 <= cores <= MAX_PLAUSIBLE_CORES:
        return f'navigator.hardwareConcurrency {cores!r} is not a core count'
    return None


def _check_screen_shape(config: Dict[str, Any], target_os: str) -> Optional[str]:
    width, height = config.get('screen.width'), config.get('screen.height')
    if not width or not height:
        return None
    if height > width:
        return f'portrait screen {width}x{height}; desktop panels are landscape'
    if width < 1024:
        return f'screen {width}x{height} is smaller than any current desktop panel'
    return None


def _check_avail_bounds(config: Dict[str, Any], target_os: str) -> Optional[str]:
    width, height = config.get('screen.width'), config.get('screen.height')
    avail_w, avail_h = config.get('screen.availWidth'), config.get('screen.availHeight')
    if width and avail_w and avail_w > width:
        return f'screen.availWidth {avail_w} exceeds screen.width {width}'
    if height and avail_h and avail_h > height:
        return f'screen.availHeight {avail_h} exceeds screen.height {height}'
    return None


def _repair_avail_bounds(config: Dict[str, Any], target_os: str) -> None:
    width, height = config.get('screen.width'), config.get('screen.height')
    if width and config.get('screen.availWidth', 0) > width:
        config['screen.availWidth'] = width
    if height and config.get('screen.availHeight', 0) > height:
        config['screen.availHeight'] = height


def _check_window_fits(config: Dict[str, Any], target_os: str) -> Optional[str]:
    for axis in ('Width', 'Height'):
        screen, outer = config.get(f'screen.{axis.lower()}'), config.get(f'window.outer{axis}')
        if screen and outer and outer > screen:
            return f'window.outer{axis} {outer} exceeds screen.{axis.lower()} {screen}'
    return None


def _repair_window_fits(config: Dict[str, Any], target_os: str) -> None:
    for axis in ('Width', 'Height'):
        screen, outer = config.get(f'screen.{axis.lower()}'), config.get(f'window.outer{axis}')
        if screen and outer and outer > screen:
            config[f'window.outer{axis}'] = config.get(f'screen.avail{axis}') or screen


RULES: List[Rule] = [
    Rule('client-hints', _check_client_hints, None),
    Rule('target-os', _check_target_os, None),
    Rule('gpu-matches-os', _check_gpu_matches_os, None),
    Rule('mac-architecture', _check_mac_architecture, _repair_mac_architecture),
    Rule('color-depth', _check_color_depth, _repair_color_depth),
    Rule('device-memory', _check_device_memory, _repair_device_memory),
    Rule('hardware-concurrency', _check_cores, None),
    Rule('screen-shape', _check_screen_shape, None),
    Rule('avail-bounds', _check_avail_bounds, _repair_avail_bounds),
    Rule('window-fits-screen', _check_window_fits, _repair_window_fits),
]

# The rules whose violation is refused at launch rather than warned about: a
# page, or the server before any page runs, reads each of them directly.
FATAL_RULES = frozenset({'client-hints', 'target-os', 'gpu-matches-os'})


def screen_is_implausible(config: Dict[str, Any]) -> bool:
    """Whether the screen is one no desktop reports (portrait, or tiny)."""
    return _check_screen_shape(config, '') is not None


def repair_screen_orientation(config: Dict[str, Any]) -> bool:
    """Turn a portrait screen landscape, keeping the panel's own dimensions.

    A recorded 1440x2560 screen is a phone panel, or a desktop one captured
    while rotated. Either way a desktop identity that claims it is
    answering a `matchMedia('(orientation: portrait)')` the way no desktop
    does, so the axes are swapped rather than the numbers invented. Run before
    the window clamps, which then bound the window to the new screen.
    """
    width, height = config.get('screen.width'), config.get('screen.height')
    if not width or not height or height <= width:
        return False
    config['screen.width'], config['screen.height'] = height, width
    avail_w, avail_h = config.get('screen.availWidth'), config.get('screen.availHeight')
    if avail_w and avail_h:
        config['screen.availWidth'], config['screen.availHeight'] = avail_h, avail_w
    return True


def _changed(before: Dict[str, Any], after: Dict[str, Any], keys: AbstractSet[str]) -> Tuple[str, ...]:
    return tuple(sorted(k for k in keys if (k in before) != (k in after) or before.get(k) != after.get(k)))



def validate(config: Dict[str, Any], target_os: str) -> List[Violation]:
    """Every invariant this identity breaks. Empty means coherent."""
    violations = []
    for rule in RULES:
        detail = rule.check(config, target_os)
        if detail:
            violations.append(Violation(rule.name, detail))
    return violations


def apply(config: Dict[str, Any], target_os: str, keep: AbstractSet[str] = frozenset()) -> List[Violation]:
    """Repair what is determined, and report what is left.

    A rule with no repair -- a Windows GPU on a macOS identity, a portrait
    screen -- cannot be corrected without inventing a machine, so it is
    returned for the caller to decide about. The caller that draws the identity
    can simply draw again.

    `keep` names the keys the caller set. A repair that would change one of
    them is not made: the caller's value stands, and the violation comes back
    with those keys in `kept` so the launcher can warn about it. A
    repair that only touches other keys (growing outerHeight to fit a caller's
    innerHeight) still runs.
    """
    kept: Dict[str, Tuple[str, ...]] = {}
    for rule in RULES:
        if not rule.repair or not rule.check(config, target_os):
            continue
        repaired = dict(config)
        rule.repair(repaired, target_os)
        if rule.adjusts and keep.intersection(rule.adjusts):
            for key in rule.adjusts:
                if key in config:
                    repaired[key] = config[key]
                else:
                    repaired.pop(key, None)
        blocked = _changed(config, repaired, keep)
        if blocked:
            kept[rule.name] = blocked
        else:
            config.clear()
            config.update(repaired)
    return [v._replace(kept=kept.get(v.rule, ())) for v in validate(config, target_os)]
