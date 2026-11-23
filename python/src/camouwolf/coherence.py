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

Camoufox's rules for Firefox-only surfaces (navigator.oscpu, Firefox's
sanitized renderer buckets, the 86px chrome height) have no Chrome counterpart
and are not here. Its CPU is Chrome's Sec-CH-UA-Arch.
"""

from typing import AbstractSet, Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

from . import clienthints
from .pkgman import LAUNCHER_CONSTANTS
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

# Core counts Apple Silicon ships; Chrome reports every core, as there is no
# SMT. 9, 13 and 15 are not Apple parts, which is why this is a list.
APPLE_SILICON_CORES = frozenset(LAUNCHER_CONSTANTS['appleSiliconCores'])

# Logical core counts of Intel Macs whose WebGL runs on the integrated GPU, and
# with a discrete one, up to the 28-core Mac Pro. An eGPU can sit behind any.
INTEL_MAC_IGP_CORES = frozenset(LAUNCHER_CONSTANTS['intelMacIgpCores'])
INTEL_MAC_DGPU_CORES = INTEL_MAC_IGP_CORES | frozenset(LAUNCHER_CONSTANTS['intelMacDgpuExtraCores'])

# The notched MacBook panels' scaled resolutions and the 24" iMac's default,
# which only Apple Silicon Macs have.
APPLE_SILICON_PANELS = frozenset(
    (width, height) for panels in LAUNCHER_CONSTANTS['appleSiliconPanels'].values() for width, height in panels
)

# window.devicePixelRatio: the scaling steps each OS's display settings offer.
# Scraped records also carry ratios such as 1.0909, which are a browser zoom
# folded into the display's scale.
PLAUSIBLE_DPR: Dict[str, Tuple[float, ...]] = {
    os_key: tuple(steps) for os_key, steps in LAUNCHER_CONSTANTS['plausibleDpr'].items()
}

# The CSS geometry a ratio scales: device pixels / devicePixelRatio.
CSS_GEOMETRY_KEYS = (
    'screen.width', 'screen.height', 'screen.availWidth', 'screen.availHeight',
    'screen.availLeft', 'screen.availTop',
    'window.outerWidth', 'window.outerHeight', 'window.innerWidth', 'window.innerHeight',
    'window.screenX', 'window.screenY',
)


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


# The CPUs each OS runs on, by architecture: who makes them.
CPU_ARCHES = ('x86', 'arm')
CPU_MANUFACTURERS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    os_key: {arch: tuple(makers) for arch, makers in by_arch.items()}
    for os_key, by_arch in LAUNCHER_CONSTANTS['cpuManufacturers'].items()
}
# Logical core counts only Intel's hybrid parts report.
INTEL_ONLY_CORE_COUNTS = frozenset(LAUNCHER_CONSTANTS['intelOnlyCoreCounts'])
# GPUs that come only with an ARM CPU, and who makes it.
_ARM_GPUS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ('Apple M', ('apple',)),
    ('Apple GPU', ('apple',)),
    ('Adreno', ('qualcomm',)),
)
# Integrated GPUs, which name the x86 CPU they are part of.
_INTEL_IGPUS = ('Intel(R) HD Graphics', 'Intel(R) UHD Graphics', 'Intel(R) Iris', 'Intel(R) Graphics',
                'Intel(R) Arc(TM) Graphics', 'Intel(R) Arc(TM) 140', 'Intel(R) Arc(TM) Pro 140')
_AMD_IGPUS = ('AMD Radeon(TM) Graphics', 'AMD Radeon(TM) Vega', 'AMD Radeon(TM) RX Vega')
# Renderers that draw on whatever CPU the machine has.
_SOFTWARE_RENDERERS = ('SwiftShader', 'llvmpipe')


def gpu_cpu(renderer: Optional[str], target_os: str) -> Tuple[Optional[str], Tuple[str, ...]]:
    """The CPU architecture a machine with this GPU has, and its possible makers.

    (None, ()) for a software renderer, which runs on any CPU. An empty tuple
    of makers leaves the maker open within the architecture.
    """
    # fpgen hands its conditions the recorded renderer lowercased.
    renderer = str(renderer or '').lower()
    if not renderer or any(name.lower() in renderer for name in _SOFTWARE_RENDERERS):
        return None, ()
    for marker, makers in _ARM_GPUS:
        if marker.lower() in renderer:
            return 'arm', makers
    if target_os == 'mac' or any(igpu.lower() in renderer for igpu in _INTEL_IGPUS):
        return 'x86', ('intel',)
    if any(igpu.lower() in renderer for igpu in _AMD_IGPUS):
        return 'x86', ('amd',)
    # A discrete card: no ARM machine on sale carries one Chrome would name.
    return 'x86', ()


def resolve_cpu(cpu: Any, target_os: str) -> Tuple[str, Optional[str]]:
    """(arch, manufacturer) for a caller's `cpu` option on `target_os`.

    Raises ValueError for anything no machine running `target_os` is: an
    unknown key, architecture or maker, or a pair such as an AMD Mac. A maker
    alone implies its architecture.
    """
    if not isinstance(cpu, dict):
        raise ValueError(f"cpu must be a dict such as {{'arch': 'x86', 'manufacturer': 'intel'}}, not {cpu!r}")
    unknown = sorted(set(cpu) - {'arch', 'manufacturer'})
    if unknown:
        raise ValueError(f"cpu takes 'arch' and 'manufacturer', not {', '.join(map(repr, unknown))}")
    arch, maker = cpu.get('arch'), cpu.get('manufacturer')
    if arch is not None and arch not in CPU_ARCHES:
        raise ValueError(f'cpu arch must be one of {CPU_ARCHES}, not {arch!r}')
    table = CPU_MANUFACTURERS[target_os]
    name = clienthints.CH_PLATFORM[target_os]
    if maker is not None:
        arches = [a for a in CPU_ARCHES if maker in table.get(a, ())]
        if not arches:
            known = sorted({m for makers in table.values() for m in makers})
            raise ValueError(f"no {name} identity has a CPU made by {maker!r}; on {name} it is one of {', '.join(known)}")
        if arch is not None and arch not in arches:
            raise ValueError(f"no {name} identity has an {arch} CPU made by {maker!r}")
        arch = arches[0]
    elif arch is not None and not table.get(arch):
        raise ValueError(f'no {name} identity has an {arch} CPU')
    return arch or 'x86', maker


def cpu_misfit(renderer: Optional[str], cores: Any, target_os: str, arch: str, maker: Optional[str]) -> Optional[str]:
    """Why a machine with this GPU and core count cannot have this CPU, or None."""
    by_gpu, makers = gpu_cpu(renderer, target_os)
    claimed = ' '.join(v for v in (maker, arch) if v)
    if by_gpu is not None and (by_gpu != arch or (maker and makers and maker not in makers)):
        return f'a {claimed} CPU beside the WebGL renderer "{renderer}"'
    if arch == 'x86' and maker and maker != 'intel' and cores in INTEL_ONLY_CORE_COUNTS:
        return f'a {claimed} CPU with hardwareConcurrency {cores}, which only Intel CPUs report'
    return None


def machine_misfit(renderer: Optional[str], target_os: str, cores: Any, width: Any, height: Any) -> Optional[str]:
    """Why no Mac with this GPU has these cores and this screen, or None.

    It checks drawn records, which are rejected, as well as finished identities.
    fpgen's records include browsers that randomise hardwareConcurrency.
    """
    renderer = str(renderer or '')
    if target_os != 'mac' or not renderer or not isinstance(cores, int):
        return None
    if 'Apple M' in renderer:
        if cores not in APPLE_SILICON_CORES:
            return f'"{renderer}" with hardwareConcurrency {cores}; Apple Silicon ships {sorted(APPLE_SILICON_CORES)}'
        return None
    allowed = INTEL_MAC_IGP_CORES if 'Intel' in renderer else INTEL_MAC_DGPU_CORES
    if cores not in allowed:
        return f'"{renderer}" with hardwareConcurrency {cores}; no Intel Mac with that GPU reports it'
    if (width, height) in APPLE_SILICON_PANELS:
        return f'"{renderer}" behind a {width}x{height} panel, which only Apple Silicon Macs have'
    return None


def _check_mac_hardware(config: Dict[str, Any], target_os: str) -> Optional[str]:
    return machine_misfit(
        _renderer(config), target_os, config.get('navigator.hardwareConcurrency'),
        config.get('screen.width'), config.get('screen.height'),
    )


def _check_architecture(config: Dict[str, Any], target_os: str) -> Optional[str]:
    arch = config.get('userAgentData.architecture')
    renderer = _renderer(config)
    expected, _ = gpu_cpu(renderer, target_os)
    if arch and expected and arch != expected:
        return (
            f'Sec-CH-UA-Arch is "{arch}" beside the WebGL renderer "{renderer}"; '
            f'that GPU is only in {expected} machines'
        )
    return None


def _repair_architecture(config: Dict[str, Any], target_os: str) -> None:
    config['userAgentData.architecture'] = gpu_cpu(_renderer(config), target_os)[0]


def _check_color_depth(config: Dict[str, Any], target_os: str) -> Optional[str]:
    depth = config.get('screen.colorDepth')
    if depth is not None and depth not in PLAUSIBLE_COLOR_DEPTH:
        return f'screen.colorDepth {depth} is not a value Chrome reports (24 or 30)'
    return None


def _repair_color_depth(config: Dict[str, Any], target_os: str) -> None:
    config['screen.colorDepth'] = 24


def _check_pixel_depth(config: Dict[str, Any], target_os: str) -> Optional[str]:
    pixel, color = config.get('screen.pixelDepth'), config.get('screen.colorDepth')
    if pixel is not None and color is not None and pixel != color:
        return f'screen.pixelDepth {pixel} differs from colorDepth {color}; Chrome reports one number for both'
    return None


def _repair_pixel_depth(config: Dict[str, Any], target_os: str) -> None:
    config['screen.pixelDepth'] = config['screen.colorDepth']


def _check_touch_points(config: Dict[str, Any], target_os: str) -> Optional[str]:
    touch = config.get('navigator.maxTouchPoints')
    if target_os == 'mac' and touch:
        return f'macOS identity with maxTouchPoints {touch}; no Mac has a touchscreen'
    return None


def _repair_touch_points(config: Dict[str, Any], target_os: str) -> None:
    config['navigator.maxTouchPoints'] = 0


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


def is_zoomed_ratio(dpr: Any, target_os: str) -> bool:
    """A devicePixelRatio no display mode of the OS offers."""
    allowed = PLAUSIBLE_DPR.get(target_os)
    if not allowed or isinstance(dpr, bool) or not isinstance(dpr, (int, float)) or dpr <= 0:
        return False
    return float(dpr) not in {float(v) for v in allowed}


def _check_device_pixel_ratio(config: Dict[str, Any], target_os: str) -> Optional[str]:
    dpr = config.get('window.devicePixelRatio')
    if dpr is not None and is_zoomed_ratio(dpr, target_os):
        return f'window.devicePixelRatio {dpr} is not a display mode {target_os} offers'
    return None


def repair_device_pixel_ratio(config: Dict[str, Any], target_os: str, keep: Iterable[str] = ()) -> None:
    """Move the ratio to the nearest display step and rescale the CSS geometry
    by the zoom that was folded in, so the screen is still the display behind
    it: 1760x990 at 1.0909 is a 1920x1080 monitor at 1. `keep` names sizes
    that are not the record's (a caller's window) and stay as they are."""
    dpr = config.get('window.devicePixelRatio')
    if not is_zoomed_ratio(dpr, target_os):
        return
    step = min(PLAUSIBLE_DPR[target_os], key=lambda v: abs(float(v) - float(dpr)))
    config['window.devicePixelRatio'] = step
    scale = float(dpr) / float(step)
    for key in CSS_GEOMETRY_KEYS:
        value = config.get(key)
        if key not in keep and isinstance(value, (int, float)) and not isinstance(value, bool):
            config[key] = int(round(value * scale))


RULES: List[Rule] = [
    Rule('client-hints', _check_client_hints, None),
    Rule('target-os', _check_target_os, None),
    Rule('gpu-matches-os', _check_gpu_matches_os, None),
    Rule('architecture', _check_architecture, _repair_architecture),
    Rule('mac-hardware', _check_mac_hardware, None),
    Rule('color-depth', _check_color_depth, _repair_color_depth),
    Rule('pixel-depth', _check_pixel_depth, _repair_pixel_depth),
    Rule('touch-points', _check_touch_points, _repair_touch_points),
    Rule('device-memory', _check_device_memory, _repair_device_memory),
    Rule('hardware-concurrency', _check_cores, None),
    Rule('device-pixel-ratio', _check_device_pixel_ratio, repair_device_pixel_ratio, CSS_GEOMETRY_KEYS),
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
