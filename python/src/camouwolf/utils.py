import hashlib
import json
import math
import os
import re
import secrets
import subprocess  # nosec
from functools import wraps
from os import environ
from os.path import abspath
from pathlib import Path
from pprint import pprint
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import orjson
from typing_extensions import TypeAlias

from . import clienthints, coherence, driver_defaults
from ._warnings import WARNINGS_DATA, LeakWarning, _warn_from_caller
from .addons import DefaultAddons, add_default_addons, confirm_paths
from .display import has_display, largest_display
from .exceptions import (
    IncoherentIdentity,
    InvalidOS,
    InvalidPropertyType,
    NonChromeFingerprint,
    UnknownProperty,
    UnsupportedOSSpoof,
)
from .fingerprints import (
    DEFAULT_MEDIA_DEVICES,
    ESSENTIAL_FONTS,
    OS_KEYS,
    OS_NAMES,
    Screen,
    clamp_screen_to_display,
    clamp_window_dimensions,
    clamp_window_position,
    fingerprint_os,
    fix_hardware_concurrency,
    fix_screen_no_taskbar,
    from_fpgen,
    generate_fingerprint,
    handle_window_size,
    raise_screen_to_modern_floor,
)
from .geolocation import geoip_allowed, get_geolocation
from .gpudisplay import GPU_SWITCHES, GpuDisplay, async_prove_gpu, prove_gpu, select_gpu
from .humanize import HumanizeSetting
from .humanize import normalize as normalize_humanize
from .ip import Proxy, public_ip
from .locales import handle_locales
from .pkgman import (
    INSTALL_DIR,
    LOCAL_DATA,
    OS_NAME,
    build_file,
    ensure_browser_profile_dir,
    installed_verstr,
    launch_path,
    selected_launch_path,
    warn_if_package_outdated,
)
from .switches import IGNORED_DEFAULT_ARGS, PLAYWRIGHT_DISABLED_FEATURES, audit_switches, merge_list_switches
from .virtdisplay import VirtualDisplay
from .webgl import chrome_gpus, usable_gpu

ListOrString: TypeAlias = Union[Tuple[str, ...], List[str], str]

# The schema of the config the browser reads: every key, with its type.
PROPERTIES_FILE: Path = LOCAL_DATA / 'data' / 'properties.json'
SYSTEM_COLORS: Dict[str, Dict[str, int]] = orjson.loads((LOCAL_DATA / 'data' / 'system-colors.json').read_bytes())

# What this host can pass for. A Linux build can render as Windows (the bundled
# Windows fonts, the UCRT math replica, patches 0019 and 0022); nothing makes a
# Linux or Windows host render as macOS, or a Windows or macOS host as Linux.
ALLOWED_OS_TARGETS: Dict[str, Tuple[str, ...]] = {
    'win': ('windows',),
    'lin': ('linux', 'windows'),
    'mac': ('macos',),
}

# How many fingerprints to draw before giving up on one whose GPU the identity
# can claim. fpgen's GPU condition already excludes software renderers, so a
# redraw only happens for a record whose ANGLE backend its own OS never uses.
MAX_FINGERPRINT_DRAWS = 20

FONT_FILE_SUFFIXES = ('.ttf', '.otf', '.ttc', '.woff', '.woff2', '.pfb')


"""
Environment: the identity travels in CAMOU_CONFIG_<n>, never in argv.
"""


def _font_scan_dirs(fonts_dir: str, os_dir: str) -> List[str]:
    """The bundled font directories an identity on `os_dir` may see.

    fontconfig scans a <dir> recursively, so the parent is never named when the
    bundle is split by OS: it would make every other OS's files candidates for
    glyph fallback.
    """
    groups_path = os.path.join(fonts_dir, 'groups.json')
    os_key = OS_KEYS.get(os_dir)
    if os_key and os.path.exists(groups_path):
        try:
            with open(groups_path, 'rb') as fh:
                read_by = json.loads(fh.read()).get('readBy', {}).get(os_key, [])
        except (OSError, ValueError):
            read_by = []
        dirs = [os.path.join(fonts_dir, g) for g in read_by if os.path.isdir(os.path.join(fonts_dir, g))]
        if dirs:
            return dirs
    if os.path.isdir(os.path.join(fonts_dir, os_dir)):
        return [os.path.join(fonts_dir, os_dir)]
    return [fonts_dir]


def _has_fonts(directories: List[str]) -> bool:
    for directory in directories:
        for _root, _dirs, files in os.walk(directory):
            if any(name.lower().endswith(FONT_FILE_SUFFIXES) for name in files):
                return True
    return False


def generate_fontconfig(executable_path: Union[str, Path], os_dir: str) -> Optional[str]:
    """
    A runtime fontconfig for an identity on `os_dir` ('linux', 'macos',
    'windows'), from the bundle beside the binary; None when the bundle ships
    no fonts for that OS.

    The bundled fonts.conf names its font directory relative to the working
    directory, which breaks when the launcher runs anywhere else, so a copy
    with absolute paths is written to the cache directory (not into the
    bundle, which is commonly read-only at launch). Loaded through
    FONTCONFIG_FILE it is the only config: the host's /etc/fonts rules, and so
    the host's fonts, are not read.
    """
    fonts_conf = build_file(executable_path, os.path.join('fontconfig', os_dir, 'fonts.conf'))
    fonts_dir = str(build_file(executable_path, 'fonts'))
    if not fonts_conf.exists():
        return None
    scan_dirs = _font_scan_dirs(fonts_dir, os_dir)
    # A bundle whose fonts were not restored still ships fonts.conf. Loading it
    # would leave the browser with no fonts at all.
    if not _has_fonts(scan_dirs):
        return None

    content = fonts_conf.read_text(encoding='utf-8').replace(
        '<dir prefix="cwd">fonts</dir>',
        '\n\t'.join(f'<dir>{d}</dir>' for d in scan_dirs),
    )
    cache_dir = INSTALL_DIR / 'fontconfig'
    cache_dir.mkdir(parents=True, exist_ok=True)
    runtime_conf = cache_dir / f'fonts-{hashlib.sha256(content.encode()).hexdigest()[:12]}.conf'
    if not runtime_conf.exists():
        runtime_conf.write_text(content, encoding='utf-8')
    return str(runtime_conf)


def config_env_vars(config_map: Dict[str, Any], chunk_size: Optional[int] = None) -> Dict[str, str]:
    """The config as CAMOU_CONFIG_1..N environment variables.

    Windows limits one variable to 32767 characters and, through some launch
    paths, to 2047, so the JSON is split there; elsewhere at 32767.
    """
    try:
        config_str = orjson.dumps(config_map).decode('utf-8')
    except (orjson.JSONEncodeError, TypeError) as e:
        raise InvalidPropertyType(f"The config cannot be serialized to JSON: {e}") from e
    if chunk_size is None:
        chunk_size = 2047 if OS_NAME == 'win' else 32767
    return {
        f'CAMOU_CONFIG_{(i // chunk_size) + 1}': config_str[i : i + chunk_size]
        for i in range(0, len(config_str), chunk_size)
    }


def get_env_vars(
    config_map: Dict[str, Any],
    user_agent_os: str,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Union[str, float, bool]]:
    """
    Gets a dictionary of environment variables for Camouwolf: the config, and
    what the identity's OS needs from the process environment.

    `path` is the executable about to launch; its bundle's fontconfig is used.
    """
    env_vars: Dict[str, Union[str, float, bool]] = dict(config_env_vars(config_map))

    # Windows resolves the sin/cos/atan2 behind DOMMatrix.rotate and canvas
    # ctx.rotate to ucrtbase; every other host resolves them to its own libm,
    # which disagrees by 1 ULP. The browser carries a bit-exact replica behind
    # this variable (browser/patches/0018-ucrt-math.patch). An environment
    # variable because every process needs the answer, before any config exists.
    if user_agent_os == 'win' and OS_NAME != 'win':
        env_vars['CAMOUWOLF_UCRT_MATH'] = '1'

    # The browser installs `timezone` as its renderers' host zone (patch 0006).
    # TZ says the same to every other process of the browser, and to ICU before
    # any config is parsed, so no process can answer with the host's zone.
    # Windows does not read TZ.
    timezone = config_map.get('timezone')
    if timezone and OS_NAME != 'win':
        env_vars['TZ'] = timezone

    if OS_NAME == 'lin' and path:
        fontconfig = generate_fontconfig(path, OS_NAMES[user_agent_os])
        if fontconfig:
            env_vars['FONTCONFIG_FILE'] = fontconfig

    return env_vars


"""
Config validation
"""


def _load_properties(path: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, str]]:
    """
    Loads the config schema: property -> its entry. A build that ships its own
    properties.json beside the binary is validated against that one; otherwise
    the schema packaged with this library is used.
    """
    prop_file = PROPERTIES_FILE
    if path:
        shipped = build_file(path, 'properties.json')
        if shipped.exists():
            prop_file = shipped
    with open(prop_file, 'rb') as f:
        prop_list = orjson.loads(f.read())
    return {prop['property']: prop for prop in prop_list}


def validate_config(config_map: Dict[str, Any], path: Optional[Union[str, Path]] = None) -> None:
    """
    Validates the config map: every key must be one the browser reads, with a
    value of its type. An unknown key raises UnknownProperty.
    """
    properties = _load_properties(path=path)

    for key, value in config_map.items():
        entry = properties.get(key)
        if not entry:
            raise UnknownProperty(
                f"Unknown property '{key}': this Camouwolf build reads no such config key. "
                f'Known properties: {", ".join(sorted(properties))}'
            )
        expected_type = entry['type']
        if not validate_type(value, expected_type):
            raise InvalidPropertyType(
                f"Invalid type for property {key}. Expected {expected_type}, got {type(value).__name__}"
            )
        if expected_type == 'array' and entry.get('items') == 'brand':
            # A UA-CH brand list: [{"brand": ..., "version": ...}, ...], in order.
            if not all(
                isinstance(item, dict)
                and set(item) == {'brand', 'version'}
                and all(isinstance(v, str) for v in item.values())
                for item in value
            ):
                raise InvalidPropertyType(
                    f'Invalid type for property {key}. Expected an array of {{"brand", "version"}} objects.'
                )
        elif expected_type == 'array' and not all(isinstance(item, str) for item in value):
            raise InvalidPropertyType(f'Invalid type for property {key}. Expected an array of strings.')
        if key == 'webGl:parameters':
            validate_webgl_parameters(value)
        if key == 'systemColors':
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in value.values()):
                raise InvalidPropertyType('Invalid systemColors: every value must be an 0xRRGGBB integer.')


def validate_webgl_parameters(parameters: Dict[str, Any]) -> None:
    """Every webGl:parameters entry is a decimal pname and a scalar; the
    browser reads nothing else (arrays included)."""
    for pname, value in parameters.items():
        if not str(pname).isdigit():
            raise InvalidPropertyType(f'Invalid webGl:parameters key {pname!r}: expected a decimal GLenum.')
        if not isinstance(value, (bool, int, float, str)):
            raise InvalidPropertyType(
                f'Invalid webGl:parameters[{pname}]: expected a number, boolean or string, '
                f'got {type(value).__name__}.'
            )


def validate_type(value: Any, expected_type: str) -> bool:
    """
    Validates the type of the value.
    """
    if expected_type == 'bool':
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if expected_type == 'str':
        return isinstance(value, str)
    if expected_type == 'int':
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if expected_type == 'uint':
        return (isinstance(value, int) or (isinstance(value, float) and value.is_integer())) and value >= 0
    if expected_type == 'double':
        return isinstance(value, (float, int))
    if expected_type == 'array':
        return isinstance(value, list)
    if expected_type == 'dict':
        return isinstance(value, dict)
    return False


"""
Identity helpers
"""


def get_target_os(config: Dict[str, Any]) -> Literal['mac', 'win', 'lin']:
    """
    Gets the OS from the config if the user agent is set,
    otherwise returns the OS of the current system.
    """
    user_agent = config.get('navigator.userAgent')
    if user_agent:
        target = clienthints.ua_os(user_agent)
        if not target:
            raise ValueError('Could not determine OS from user agent')
        return target  # type: ignore[return-value]
    return OS_NAME


def check_valid_os(os: ListOrString) -> None:
    """
    Checks if the target OS is valid.
    """
    if not isinstance(os, str):
        for os_name in os:
            check_valid_os(os_name)
        return
    # Assert that the OS is lowercase
    if not os.islower():
        raise InvalidOS(f"OS values must be lowercase: '{os}'")
    # Assert that the OS is supported by Camouwolf
    if os not in ('windows', 'macos', 'linux'):
        raise InvalidOS(f"Camouwolf does not support the OS: '{os}'")


def check_os_policy(names: List[str], allow_os_mismatch: Optional[bool]) -> None:
    """Refuse an identity on an OS this host cannot pass for."""
    if allow_os_mismatch:
        return
    allowed = ALLOWED_OS_TARGETS[OS_NAME]
    bad = [name for name in names if name not in allowed]
    if bad:
        raise UnsupportedOSSpoof(
            f"Camouwolf cannot pass a {OS_NAMES[OS_NAME]} host off as: {', '.join(bad)}. "
            'Supported host -> identity pairs: windows -> windows, linux -> linux, '
            'linux -> windows, macos -> macos. Pass allow_os_mismatch=True to override.'
        )


def merge_into(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Merges new keys/values from the source dictionary into the target dictionary.
    Given that the key does not exist in the target dictionary.
    """
    for key, value in source.items():
        if key not in target:
            target[key] = value


def is_domain_set(config: Dict[str, Any], *properties: str) -> bool:
    """
    Checks if a domain is set in the config.
    """
    for prop in properties:
        # A trailing . or : makes it a prefix
        if prop[-1] in ('.', ':'):
            if any(key.startswith(prop) for key in config):
                return True
        elif prop in config:
            return True
    return False


def warn_kept_incoherent(violations: List[coherence.Violation]) -> None:
    """
    Warns for each coherence violation the launch left in place because the
    caller set the values involved. Camouwolf does not overwrite what the caller
    passed, but an identity no real machine presents is detectable.
    """
    for violation in violations:
        if violation.kept:
            _warn_from_caller(
                WARNINGS_DATA['caller_value_incoherent'].format(
                    keys=', '.join(violation.kept), detail=violation.detail
                ),
                LeakWarning,
            )


def warn_manual_config(config: Dict[str, Any]) -> None:
    """
    Warns the user if they are manually setting properties that Camouwolf already sets internally.
    """
    if is_domain_set(config, 'navigator.languages'):
        LeakWarning.warn('locale', False)
    if is_domain_set(config, 'geolocation:', 'timezone'):
        LeakWarning.warn('geolocation', False)
    if any(key.startswith(('navigator.', 'userAgentData.')) and key != 'navigator.languages' for key in config):
        LeakWarning.warn('navigator', False)
    if is_domain_set(config, 'screen.', 'window.'):
        LeakWarning.warn('viewport', False)


def check_custom_fingerprint(fingerprint: Dict[str, Any]) -> None:
    """
    Asserts that the passed fingerprint is a Chrome fingerprint, and warns the
    user that passing their own fingerprint is not recommended.
    """
    user_agent = (fingerprint.get('navigator') or {}).get('userAgent') or ''
    if 'Chrome/' not in user_agent or re.search(r'Edg/|OPR/|Firefox/', user_agent):
        raise NonChromeFingerprint(
            'This fingerprint is not from Chrome '
            f'(user agent: {user_agent or "none"!r}). Camouwolf is Chromium; a fingerprint from another '
            'browser WILL lead to detection. Generate one with fpgen and browser="Chrome".'
        )
    LeakWarning.warn('custom_fingerprint', False)


def resolve_version(executable_path: Optional[Union[str, Path]] = None) -> str:
    """The Chromium version ('152.0.7977.42') of the build about to launch.

    The named executable's own version.json, or what the binary answers to
    --version; the installed release when no executable is named. A build that
    states no version raises, since claiming another build's would leak in the
    user agent and the client hints.
    """
    if not executable_path:
        return installed_verstr().split('-', 1)[0]
    version_file = build_file(executable_path, 'version.json')
    if version_file.exists():
        version = orjson.loads(version_file.read_bytes()).get('version')
        if isinstance(version, str) and re.fullmatch(r'\d+(\.\d+){1,3}', version):
            return version
    try:
        out = subprocess.run(  # nosec
            [str(executable_path), '--version'], capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'Could not read the Chromium version of {executable_path}: {error}') from error
    match = re.search(r'\d+\.\d+\.\d+\.\d+', out)
    if not match:
        raise ValueError(
            f'{executable_path} states no Chromium version: it has no version.json beside it and '
            'answered --version with nothing recognisable.'
        )
    return match.group(0)


def keyboard_layout_map(target_os: str) -> Optional[List[str]]:
    """The navigator.keyboard.getLayoutMap() of a US QWERTY keyboard on the
    target OS, as "code=key" entries. None on macOS, where the host's map is
    left as it is."""
    if target_os == 'mac':
        return None
    entries = [f'Key{c.upper()}={c}' for c in 'abcdefghijklmnopqrstuvwxyz']
    entries += [f'Digit{d}={d}' for d in '0123456789']
    entries += [
        'Backquote=`', 'Minus=-', 'Equal==', 'BracketLeft=[', 'BracketRight=]', 'Backslash=\\',
        'Semicolon=;', "Quote='", 'Comma=,', 'Period=.', 'Slash=/',
        'IntlBackslash=\\' if target_os == 'win' else 'IntlBackslash=<',
    ]
    return entries


def languages_for(locale_config: Dict[str, Any]) -> Optional[List[str]]:
    """navigator.languages for a resolved locale: every listed locale, or the
    primary one and its bare language ("en-US", "en"), as Chrome's default."""
    listed = locale_config.get('locale:all')
    if listed:
        return [tag.strip() for tag in listed.split(',') if tag.strip()]
    language, region = locale_config.get('locale:language'), locale_config.get('locale:region')
    if not language:
        return None
    primary = f'{language}-{region}' if region else language
    return list(dict.fromkeys([primary, language]))


def write_chromium_prefs(user_data_dir: Union[str, Path], prefs: Dict[str, Any]) -> Path:
    """Merge `prefs` into the profile's Default/Preferences file.

    Keys are Chromium's dotted pref paths ("credentials_enable_service",
    "profile.default_content_setting_values.notifications") or nested dicts; a
    dotted key is expanded into the nested form Chromium stores.
    """
    path = Path(user_data_dir) / 'Default' / 'Preferences'
    try:
        current = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        current = {}

    def merge(target: Dict[str, Any], source: Dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    for key, value in prefs.items():
        nested: Any = value
        for part in reversed(str(key).split('.')):
            nested = {part: nested}
        merge(current, nested)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps(current))
    return path


"""
What a set of launch options carries
"""


def _camou_config_blob(from_options: Dict[str, Any]) -> str:
    env = from_options.get('env') or {}
    chunks = [(int(k.rsplit('_', 1)[1]), v) for k, v in env.items() if re.fullmatch(r'CAMOU_CONFIG_\d+', k)]
    return ''.join(v for _, v in sorted(chunks))


def launch_config(from_options: Dict[str, Any]) -> Dict[str, Any]:
    """The config a set of launch options carries in its environment."""
    blob = _camou_config_blob(from_options)
    try:
        config = orjson.loads(blob) if blob else {}
    except orjson.JSONDecodeError:
        return {}
    return config if isinstance(config, dict) else {}


def launch_os(from_options: Dict[str, Any]) -> str:
    """The OS ('macos', 'windows' or 'linux') of the identity these launch
    options claim, the host's when they claim no user agent."""
    return OS_NAMES[get_target_os(launch_config(from_options))]


def launch_geolocation(from_options: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The position these launch options place the browser at, as Playwright's
    `geolocation` option, or None."""
    config = launch_config(from_options)
    latitude, longitude = config.get('geolocation:latitude'), config.get('geolocation:longitude')
    if latitude is None or longitude is None:
        return None
    geolocation = {'latitude': latitude, 'longitude': longitude}
    if config.get('geolocation:accuracy'):
        geolocation['accuracy'] = config['geolocation:accuracy']
    return geolocation


def pinned_core_count(from_options: Dict[str, Any]) -> Optional[int]:
    """The core count the browser must be pinned to for these launch options,
    or None: the identity's navigator.hardwareConcurrency when this host can
    honour it (see cpu_affinity), so a page measuring parallelism sees the
    reported number."""
    from .cpu_affinity import host_cores, supported

    if not supported():
        return None
    value = launch_config(from_options).get('navigator.hardwareConcurrency')
    cores = host_cores()
    if isinstance(value, int) and cores and 1 <= value < len(cores):
        return value
    return None


def driver_pid(playwright: Any) -> Optional[int]:
    """PID of the Playwright driver that will spawn the browser (its children
    inherit the CPU affinity we set on it)."""
    try:
        impl = getattr(playwright, '_impl_obj', playwright)
        return int(impl._connection._transport._proc.pid)
    except Exception:
        return None


"""
Context defaults
"""

# Playwright emulates four media features on every context it creates, whether
# or not the caller asked: `colorScheme` defaults to "light" and reducedMotion /
# forcedColors / contrast to their no-preference values. That is an override,
# not a passthrough: the page then reports it whatever the host is set to.
# "no-override" is Playwright's own opt-out: it sends no emulation at all and
# the browser answers for itself.
STOCK_MEDIA_DEFAULTS = {
    'color_scheme': 'no-override',
    'reduced_motion': 'no-override',
    'forced_colors': 'no-override',
    'contrast': 'no-override',
}

# Context options that would make one context of the browser a different
# machine from the rest of it. The identity is the process's: workers, the
# network stack and the GPU process answer from CAMOU_CONFIG whatever
# Playwright emulates in one context's pages, so a page comparing its window
# with a worker reads two user agents.
PER_CONTEXT_IDENTITY_OPTIONS: Dict[str, str] = {
    'user_agent': 'navigator.userAgent comes from the launch identity; set `config`, `os` or `fingerprint` at launch',
    'is_mobile': 'Camouwolf only has desktop identities',
    'device_scale_factor': 'it changes the device pixel ratio without the screen the identity claims',
    'screen': 'the screen comes from the launch identity; use the `screen` launch option',
    'has_touch': 'the launch identity reports no touchscreen',
}


def _wrap_context_factories(target: Any, adjust: Any) -> Any:
    """Run `adjust(kwargs)` before every new_page()/new_context() call on `target`."""
    for name in ('new_page', 'new_context'):
        original = getattr(target, name, None)
        if original is None:
            continue

        def wrap(original: Any) -> Any:
            @wraps(original)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                adjust(kwargs)
                # Works for both sync and async: async returns the coroutine
                # unawaited, and the caller awaits it as usual.
                return original(*args, **kwargs)

            return wrapper

        setattr(target, name, wrap(original))
    return target


def per_context_identity_problems(kwargs: Dict[str, Any]) -> List[str]:
    """The context options in `kwargs` that would give one context another
    identity than the browser's, each with the reason."""
    return [
        f'{option}= cannot be set per context: {why}'
        for option, why in PER_CONTEXT_IDENTITY_OPTIONS.items()
        if kwargs.get(option) is not None
    ]


def refuse_per_context_identity(kwargs: Dict[str, Any]) -> None:
    """Raise IncoherentIdentity for a context option that would give one
    context another identity than the browser's."""
    problems = per_context_identity_problems(kwargs)
    if problems:
        raise IncoherentIdentity(
            '; '.join(problems)
            + '. Workers, request headers and the GPU process would still answer as the launch '
            'identity. Launch another browser for another identity.'
        )


def context_defaults(geolocation: Optional[Dict[str, float]]) -> Any:
    """The adjustment every new_page()/new_context() of a Camouwolf browser gets.

    - no_viewport: Playwright's default 1280x720 viewport is a device-metrics
      override, which replaces the window size the identity launched with and
      reports the viewport as the screen. Without it the page reads the real
      window. A caller's viewport= or no_viewport= wins.
    - the host's own media features instead of Playwright's emulated ones.
    - the launch's geolocation (geoip, or geolocation:* in config), granted, as
      a browser whose user chose "Always allow" has it.
    - a LeakWarning for an option that gives the context another identity than
      the browser's (NewContext refuses those outright).
    """

    def adjust(kwargs: Dict[str, Any]) -> None:
        for problem in per_context_identity_problems(kwargs):
            _warn_from_caller(WARNINGS_DATA['per_context_identity'].format(problem=problem), LeakWarning)
        if 'viewport' not in kwargs and 'no_viewport' not in kwargs:
            kwargs['no_viewport'] = True
        for option, value in STOCK_MEDIA_DEFAULTS.items():
            kwargs.setdefault(option, value)
        if geolocation and 'geolocation' not in kwargs:
            kwargs['geolocation'] = dict(geolocation)
            permissions = list(kwargs.get('permissions') or [])
            if 'geolocation' not in permissions:
                permissions.append('geolocation')
            kwargs['permissions'] = permissions

    return adjust


# Camoufox's NewContext options that draw a second identity for one context.
PER_CONTEXT_FINGERPRINT_OPTIONS = ('preset', 'os', 'ff_version', 'chrome_version', 'webrtc_ip')


def new_context_options(
    context_kwargs: Dict[str, Any],
    proxy: Optional[Dict[str, str]] = None,
    geolocation: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """The new_context() options NewContext passes: the caller's, after
    refusing any that would give the context an identity of its own."""
    for option in PER_CONTEXT_FINGERPRINT_OPTIONS:
        if context_kwargs.get(option) is not None:
            raise NotImplementedError(
                f'NewContext({option}=) is not implemented in Camouwolf: the browser reads one identity '
                'per process (CAMOU_CONFIG), so a context cannot have its own fingerprint. Launch '
                'another browser for another identity.'
            )
    refuse_per_context_identity(context_kwargs)
    options = dict(context_kwargs)
    if proxy:
        options['proxy'] = proxy
    if geolocation:
        options['geolocation'] = geolocation
        permissions = list(options.get('permissions') or [])
        if 'geolocation' not in permissions:
            permissions.append('geolocation')
        options['permissions'] = permissions
    return options


def persistent_context_options(from_options: Dict[str, Any]) -> Dict[str, Any]:
    """Launch options for launch_persistent_context(). The launch itself creates
    the context, so the defaults new_context() would get go into the options."""
    options = dict(from_options)
    context_defaults(launch_geolocation(from_options))(options)
    return options


def warn_addons_without_profile(from_options: Dict[str, Any]) -> None:
    """Warn that a non-persistent launch carries extensions its contexts will
    not run."""
    if any(str(arg).startswith('--load-extension=') for arg in from_options.get('args') or []):
        _warn_from_caller(WARNINGS_DATA['addons_need_persistent_context'], RuntimeWarning)


def attach_context_defaults(browser: Any, from_options: Dict[str, Any]) -> None:
    """Give every new_page()/new_context() of `browser` Camouwolf's defaults."""
    _wrap_context_factories(browser, context_defaults(launch_geolocation(from_options)))
    driver_defaults.mark(browser)


"""
Virtual displays
"""


def start_virtual_display(
    headless: Any, debug: Optional[bool], kwargs: Dict[str, Any]
) -> Tuple[Optional[VirtualDisplay], Any]:
    """Start the display headless='virtual' or 'virtual-gpu' asks for.

    Pops the display's own options from `kwargs` and points the launch at the
    display. Returns the display, or None, and the headless value to launch with.
    """
    gpu = kwargs.pop('gpu', None)
    require_gpu = kwargs.pop('require_gpu', None)
    if headless == 'virtual-gpu':
        display: VirtualDisplay = GpuDisplay(
            select_gpu(gpu), debug=bool(debug), require_gpu=require_gpu is not False
        )
        # The caller's own switches come after, and win.
        kwargs['args'] = [*GPU_SWITCHES, *(kwargs.get('args') or [])]
    elif gpu is not None or require_gpu is not None:
        raise ValueError("gpu and require_gpu apply to headless='virtual-gpu' only")
    elif headless == 'virtual':
        display = VirtualDisplay(debug=bool(debug))
    else:
        return None, headless
    # launch_options sizes the display to the identity and starts it.
    kwargs['virtual_display'] = display
    return display, False


async def async_attach_vd(browser: Any, virtual_display: Optional[VirtualDisplay] = None) -> Any:
    """
    Attaches the virtual display to the async browser cleanup, and on a GPU
    display checks the browser renders on the GPU.
    """
    if not virtual_display:  # Skip if no virtual display is provided
        return browser

    _close = browser.close

    async def new_close(*args: Any, **kwargs: Any):
        try:
            await _close(*args, **kwargs)
        finally:
            virtual_display.kill()

    browser.close = new_close

    if isinstance(virtual_display, GpuDisplay):
        try:
            await async_prove_gpu(browser, virtual_display)
        except BaseException:
            await browser.close()
            raise

    return browser


def sync_attach_vd(browser: Any, virtual_display: Optional[VirtualDisplay] = None) -> Any:
    """
    Attaches the virtual display to the sync browser cleanup, and on a GPU
    display checks the browser renders on the GPU.
    """
    if not virtual_display:  # Skip if no virtual display is provided
        return browser

    _close = browser.close

    def new_close(*args: Any, **kwargs: Any):
        try:
            _close(*args, **kwargs)
        finally:
            virtual_display.kill()

    browser.close = new_close

    if isinstance(virtual_display, GpuDisplay):
        try:
            prove_gpu(browser, virtual_display)
        except BaseException:
            browser.close()
            raise

    return browser


"""
The launch
"""


def draw_fingerprint(
    os: Optional[ListOrString],
    screen: Optional[Screen],
    window: Optional[Tuple[int, int]],
    webgl_config: Optional[Tuple[str, str]],
) -> Dict[str, Any]:
    """Draw an fpgen Chrome fingerprint whose GPU the identity may claim.

    Without `webgl_config`, any recorded hardware GPU on a backend its OS uses.
    With it, a record of exactly that device: its limits, screen and fonts are
    then the ones a machine with that GPU reported.
    """
    names = [os] if isinstance(os, str) else list(os or [])
    if webgl_config:
        vendor, renderer = webgl_config
        if not any((vendor, renderer) in chrome_gpus(name) for name in names):
            possible = sorted(pair for name in names for pair in chrome_gpus(name))
            raise ValueError(
                f'No recorded Chrome device with vendor {vendor!r} and renderer {renderer!r} on '
                f'{", ".join(names)}. Possible pairs: {possible}'
            )

        # fpgen hands a predicate the recorded value lowercased.
        wanted = (vendor.lower(), renderer.lower())

        def gpu(value: Any) -> bool:
            return isinstance(value, dict) and (
                str(value.get('vendor')).lower(), str(value.get('renderer')).lower()
            ) == wanted

    else:
        gpu = usable_gpu

    for _ in range(MAX_FINGERPRINT_DRAWS):
        fingerprint = generate_fingerprint(window=window, screen=screen, os=os or None, gpu=gpu)
        drawn_os = fingerprint_os(fingerprint)
        # Camouwolf builds are x86-64 desktops. fpgen also records Chrome on ARM
        # Linux ("Linux armv81", "Linux aarch64"), whose platform a build that
        # computes as x86-64 cannot back up.
        platform = (fingerprint.get('navigator') or {}).get('platform')
        if platform != clienthints.NAV_PLATFORM.get(drawn_os):
            continue
        if usable_gpu(fingerprint.get('gpu'), drawn_os):
            return fingerprint
    raise RuntimeError(
        f'fpgen drew no desktop Chrome fingerprint with a usable GPU for os={os!r} in '
        f'{MAX_FINGERPRINT_DRAWS} draws'
    )


def launch_options(
    *,
    config: Optional[Dict[str, Any]] = None,
    os: Optional[ListOrString] = None,
    block_images: Optional[bool] = None,
    block_webrtc: Optional[bool] = None,
    block_webgl: Optional[bool] = None,
    disable_coop: Optional[bool] = None,
    webgl_config: Optional[Tuple[str, str]] = None,
    cpu: Optional[Dict[str, str]] = None,
    geoip: Optional[Union[str, bool]] = None,
    geoip_db: Optional[str] = None,
    humanize: HumanizeSetting = None,
    locale: Optional[Union[str, List[str]]] = None,
    addons: Optional[List[str]] = None,
    fonts: Optional[List[str]] = None,
    custom_fonts_only: Optional[bool] = None,
    exclude_addons: Optional[List[DefaultAddons]] = None,
    screen: Optional[Screen] = None,
    window: Optional[Tuple[int, int]] = None,
    fingerprint: Optional[Dict[str, Any]] = None,
    fingerprint_preset: Optional[Union[bool, Dict[str, Any]]] = None,
    chrome_version: Optional[int] = None,
    ff_version: Optional[int] = None,
    headless: Optional[bool] = None,
    main_world_eval: Optional[bool] = None,
    allow_addon_new_tab: Optional[bool] = None,
    executable_path: Optional[Union[str, Path]] = None,
    browser: Optional[str] = None,
    chromium_prefs: Optional[Dict[str, Any]] = None,
    firefox_user_prefs: Optional[Dict[str, Any]] = None,
    proxy: Optional[Dict[str, str]] = None,
    enable_cache: Optional[bool] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, Union[str, float, bool]]] = None,
    i_know_what_im_doing: Optional[bool] = None,
    debug: Optional[bool] = None,
    virtual_display: Optional[Union[str, VirtualDisplay]] = None,
    virtual_display_size: Optional[Tuple[int, int]] = None,
    pin_cpu_cores: Optional[bool] = None,
    canvas_noise: Optional[bool] = None,
    allow_os_mismatch: Optional[bool] = None,
    **launch_options: Any,
) -> Dict[str, Any]:
    """
    Builds the Playwright launch options for a Camouwolf browser.
    Accepts all Playwright Chromium launch options, along with the following:

    Parameters:
        config (Optional[Dict[str, Any]]):
            Camouwolf properties to use; they win over generated values. An
            unknown key raises UnknownProperty (see data/properties.json).
        os (Optional[ListOrString]):
            Operating system of the identity: "windows", "macos", "linux", or a
            list to choose from. Defaults to what this host can pass for: its
            own OS, and Windows on a Linux host. Another OS raises
            UnsupportedOSSpoof unless `allow_os_mismatch=True`.
        block_images (Optional[bool]):
            Whether to block all images.
        block_webrtc (Optional[bool]):
            Keep WebRTC from using any route but the proxy's. On by default
            with a proxy; False with a proxy warns.
        block_webgl (Optional[bool]):
            Whether to block WebGL. To prevent leaks, only use this for special cases.
        disable_coop (Optional[bool]):
            Not implemented: raises NotImplementedError. Playwright reaches
            cross-origin iframes in Chromium without it.
        geoip (Optional[Union[str, bool]]):
            Calculate longitude, latitude, timezone, country, & locale based on the IP address.
            Pass the target IP address to use, or `True` to find the IP address automatically.
        geoip_db (Optional[str]):
            Name of the GeoIP database to use (e.g., "GeoIP AIO by daijro").
        humanize (Optional[Union[bool, dict]]):
            Driver-side humanization. True is `myuri()` on every channel; a dict takes
            `myuri()` or `raw()` from `camouwolf.humanize` per channel.
            Applied by Camouwolf()/NewBrowser(); only validated here.
        locale (Optional[Union[str, List[str]]]):
            Locale(s) to use. The first is the browser's UI and Intl locale; all
            of them are navigator.languages and the Accept-Language header.
        addons (Optional[List[str]]):
            Unpacked Chrome extensions (directories holding a manifest.json) to load.
            Chromium runs extensions in the browser's own profile only: use
            persistent_context=True. Contexts of a non-persistent launch are
            incognito-like and run none (the launch warns).
        fonts (Optional[List[str]]):
            Font families to report installed, in addition to the identity's own.
        custom_fonts_only (Optional[bool]):
            Report only `fonts` installed, without the identity's own.
        exclude_addons (Optional[List[DefaultAddons]]):
            Default addons to exclude. Passed as a list of camouwolf.DefaultAddons enums.
        screen (Optional[Screen]):
            Constrains the screen dimensions of the generated fingerprint.
        window (Optional[Tuple[int, int]]):
            Set a fixed outer window size instead of the fingerprint's.
        fingerprint (Optional[Dict[str, Any]]):
            An fpgen Chrome fingerprint to use instead of drawing one.
        fingerprint_preset (Optional[Union[bool, Dict[str, Any]]]):
            Not implemented: raises NotImplementedError. Camouwolf bundles no presets.
        chrome_version (Optional[int]):
            Chromium milestone to claim. Defaults to the browser's own; another
            one disagrees with what the binary can do, and warns.
        ff_version (Optional[int]):
            Camoufox's option; raises NotImplementedError naming chrome_version.
        headless (Optional[bool]):
            Whether to run Chromium's own headless mode. Defaults to False.
            On Linux, passing headless='virtual' to Camouwolf, AsyncCamouwolf or
            launch_server runs a headed browser on Xvfb, and
            headless='virtual-gpu' on a headless Xorg server bound to one GPU.
        main_world_eval (Optional[bool]):
            Accepted for Camoufox compatibility. Playwright evaluates in the
            page's main world on Chromium, with or without the "mw:" prefix,
            which JavaScript reads as a label.
        allow_addon_new_tab (Optional[bool]):
            Not implemented: raises NotImplementedError when set.
        executable_path (Optional[Union[str, Path]]):
            Custom Camouwolf browser executable path. CAMOUWOLF_EXECUTABLE_PATH
            in the environment does the same.
        browser (Optional[str]):
            Select a specific installed browser version ("official/alpha.1", ...).
        chromium_prefs (Optional[Dict[str, Any]]):
            Chromium preferences, merged into the profile's Preferences file
            before launch. Needs a persistent context (`user_data_dir`);
            without one it raises ValueError.
        firefox_user_prefs (Optional[Dict[str, Any]]):
            Camoufox's option; raises NotImplementedError naming chromium_prefs and args.
        proxy (Optional[Dict[str, str]]):
            Proxy to use for the browser.
            Note: If geoip is True, a request will be sent through this proxy to find the target IP.
        enable_cache (Optional[bool]):
            Keep the HTTP disk cache. Off by default.
        args (Optional[List[str]]):
            Arguments to pass to the browser. Automation markers and switches
            that change web-visible behaviour are dropped; a switch that would
            contradict the identity (--user-agent, --lang, --window-size, ...)
            raises ValueError.
        env (Optional[Dict[str, Union[str, float, bool]]]):
            Environment variables to set.
        debug (Optional[bool]):
            Prints the config being sent to Camouwolf.
        virtual_display (Optional[Union[str, VirtualDisplay]]):
            Virtual display number. Ex: ':99'. This is handled by Camouwolf & AsyncCamouwolf,
            which pass a display not yet started; it is sized to the identity and started here.
        virtual_display_size (Optional[Tuple[int, int]]):
            The virtual display's size in device pixels; the identity's screen,
            at its devicePixelRatio, is kept within it.
        pin_cpu_cores (Optional[bool]):
            Keep the fingerprint's own core count by pinning the browser to that
            many cores. Off by default: the host's count is reported.
        webgl_config (Optional[Tuple[str, str]]):
            Use a specific WebGL vendor/renderer pair. It must be one fpgen has
            recorded from Chrome on `os` (camouwolf.webgl.chrome_gpus); the
            whole identity is then drawn from a machine with that GPU.
        cpu (Optional[Dict[str, str]]):
            Not implemented: raises NotImplementedError. The browser computes as
            the host's CPU does.
        canvas_noise (Optional[bool]):
            Perturb canvas readback with a per-identity seed (canvas:seed and
            canvas:randomizeReadback). Stock Chrome does not, so it warns. Off by default.
        allow_os_mismatch (Optional[bool]):
            Allow an identity on an OS this host cannot pass for.
        i_know_what_im_doing (Optional[bool]):
            Silence warnings, and drop conflicting switches instead of refusing them.
        **launch_options (Dict[str, Any]):
            Additional Playwright Chromium launch options.
    """
    # ---- options with no Chromium equivalent ------------------------------
    if firefox_user_prefs:
        raise NotImplementedError(
            'firefox_user_prefs has no Chromium equivalent. Pass command-line switches in `args`, or '
            'Chromium preferences in `chromium_prefs` together with persistent_context=True and a user_data_dir.'
        )
    if ff_version is not None:
        raise NotImplementedError("ff_version is Camoufox's option. Use chrome_version (a Chromium milestone).")
    if cpu is not None:
        raise NotImplementedError(
            'cpu= is not implemented in Camouwolf: the browser has no cpu:arch / cpu:manufacturer '
            'keys and computes as the host CPU does.'
        )
    if fingerprint_preset:
        raise NotImplementedError(
            'fingerprint_preset is not implemented in Camouwolf: no presets are bundled. '
            'Leave it unset to draw an fpgen fingerprint, or pass one as `fingerprint`.'
        )
    if disable_coop:
        raise NotImplementedError(
            'disable_coop is not implemented in Camouwolf: Chromium has no switch for it, and none is '
            'needed. Playwright reaches elements inside cross-origin iframes on Chromium as it is.'
        )
    if allow_addon_new_tab:
        raise NotImplementedError(
            'allow_addon_new_tab is not implemented in Camouwolf: the launcher does not stop an '
            'extension from opening a tab, so there is nothing to allow.'
        )
    # main_world_eval: nothing to switch on. See the docstring.

    # The launch fills these in, so it works on copies: a caller's mapping
    # reused for a second launch would otherwise carry the first identity's
    # values in, as if the caller had set them.
    config = dict(config or {})
    addons = list(addons or [])
    args = list(args or [])

    if headless is None:
        headless = False
    if i_know_what_im_doing is None:
        i_know_what_im_doing = False
    normalize_humanize(humanize)

    # Keep per-launch overrides isolated from the process environment and from
    # mappings supplied by callers. In particular, DISPLAY must not outlive the
    # virtual display that owns it.
    env = dict(environ) if env is None else dict(env)
    ensure_browser_profile_dir(env)

    # ---- the binary --------------------------------------------------------
    # CAMOUWOLF_EXECUTABLE_PATH points every launch at a specific build without
    # threading the path through each call site.
    _env_executable = environ.get('CAMOUWOLF_EXECUTABLE_PATH', '').strip()
    if browser:
        if executable_path or _env_executable:
            raise ValueError(
                f'browser={browser!r} selects an installed build, but '
                f'{"executable_path" if executable_path else "CAMOUWOLF_EXECUTABLE_PATH"} '
                'names a binary too. Pass one of them.'
            )
        executable_path = selected_launch_path(browser)
    elif executable_path is None and _env_executable:
        executable_path = _env_executable
    if executable_path is not None:
        executable_path = Path(abspath(str(executable_path)))
    # launch_path() downloads the browser when none is installed.
    browser_binary = str(executable_path) if executable_path else launch_path()
    warn_if_package_outdated()

    # ---- display -----------------------------------------------------------
    owned_display = virtual_display if isinstance(virtual_display, VirtualDisplay) else None
    if owned_display and owned_display.fixed:
        virtual_display_size = owned_display.size
    if virtual_display:
        if not owned_display:
            env['DISPLAY'] = virtual_display
        # The virtual display is X11. A host session that exports
        # WAYLAND_DISPLAY would otherwise send the window to the real desktop.
        env['GDK_BACKEND'] = 'x11'
        env.pop('WAYLAND_DISPLAY', None)
        args.insert(0, '--ozone-platform=x11')

    # ---- the identity ------------------------------------------------------
    if not i_know_what_im_doing:
        warn_manual_config(config)
    _caller_keys = frozenset(config)
    _user_set_screen_window = is_domain_set(config, 'screen.', 'window.')

    if os:
        check_valid_os(os)
        check_os_policy([os] if isinstance(os, str) else list(os), allow_os_mismatch)
    elif webgl_config:
        raise ValueError('OS must be set when using webgl_config')
    elif not allow_os_mismatch:
        os = list(ALLOWED_OS_TARGETS[OS_NAME])

    # The identity has to match the Chromium that actually runs.
    full_version = resolve_version(browser_binary)
    if chrome_version is not None and int(chrome_version) != int(full_version.split('.', 1)[0]):
        LeakWarning.warn('chrome_version', i_know_what_im_doing)
        full_version = f'{int(chrome_version)}.0.0.0'
    milestone = int(full_version.split('.', 1)[0])

    # A real display bounds the screen the identity may claim; so does a
    # virtual one of fixed size, which the real window has to fit on. One the
    # launcher sizes is fitted to the identity instead.
    display_bound: Optional[Screen] = None
    if headless is not True:
        if virtual_display_size:
            display_bound = Screen(max_width=virtual_display_size[0], max_height=virtual_display_size[1])
        elif not virtual_display and has_display(env):
            display = largest_display()
            if display:
                display_bound = Screen(max_width=display.width, max_height=display.height)

    # A caller who names the user agent themselves has pinned the identity.
    if fingerprint is None and 'navigator.userAgent' not in config:
        fingerprint = draw_fingerprint(os, screen or display_bound, window, webgl_config)
    elif fingerprint is not None:
        if webgl_config:
            raise ValueError('webgl_config draws the fingerprint; it cannot be combined with `fingerprint`')
        if not i_know_what_im_doing:
            check_custom_fingerprint(fingerprint)
        if window:
            fingerprint = {
                **fingerprint,
                'screen': dict(fingerprint.get('screen') or {}),
                'window': dict(fingerprint.get('window') or {}),
            }
            handle_window_size(fingerprint, *window)
    elif webgl_config:
        raise ValueError('webgl_config draws the fingerprint; it cannot be combined with a user agent in `config`')

    if fingerprint is not None:
        merge_into(config, from_fpgen(fingerprint, chrome_version=milestone))

    target_os = get_target_os(config)
    check_os_policy([OS_NAMES[target_os]], allow_os_mismatch)

    config.setdefault(
        'navigator.userAgent',
        clienthints.build_user_agent(
            clienthints.ChromeIdentity(milestone=milestone, full_version=full_version, os=target_os)
        ),
    )
    config.setdefault('navigator.platform', clienthints.NAV_PLATFORM[target_os])
    clienthints.fill_client_hints(config, target_os, full_version)

    # The core count a page can measure is the host's, unless the browser is
    # pinned to the drawn one.
    if 'navigator.hardwareConcurrency' not in _caller_keys:
        fix_hardware_concurrency(config, can_pin=bool(pin_cpu_cores))

    # ---- fonts -------------------------------------------------------------
    if custom_fonts_only:
        if not fonts:
            raise ValueError('custom_fonts_only=True needs `fonts`')
        LeakWarning.warn('custom_fonts_only', i_know_what_im_doing)
        config['fonts'] = list(dict.fromkeys(fonts))
    elif fonts:
        base = config.get('fonts') or ESSENTIAL_FONTS[target_os]
        config['fonts'] = list(dict.fromkeys([*base, *fonts]))

    # ---- devices, keyboard, colours ---------------------------------------
    config.setdefault('mediaDevices:enumerated', list(DEFAULT_MEDIA_DEVICES[target_os]))
    layout = keyboard_layout_map(target_os)
    if layout:
        config.setdefault('keyboard.layoutMap', layout)
    colors = SYSTEM_COLORS.get(OS_NAMES[target_os])
    if colors:
        config.setdefault('systemColors', dict(colors))

    # ---- canvas ------------------------------------------------------------
    if canvas_noise:
        LeakWarning.warn('canvas_noise', i_know_what_im_doing)
        config.setdefault('canvas:seed', secrets.randbelow(2**32 - 1) + 1)
        config.setdefault('canvas:randomizeReadback', True)
    else:
        # Stock Chrome returns the pixels it drew.
        config.setdefault('canvas:seed', 0)
        config.setdefault('canvas:randomizeReadback', False)
    config.setdefault('canvas:quantizeTextMetrics', False)
    config.setdefault('AudioContext:randomizeSamples', False)

    # ---- place: geoip, locale ---------------------------------------------
    locale_config: Dict[str, Any] = {}
    if geoip:
        geoip_allowed()  # Assert that geoip is allowed
        if geoip is True:
            geoip = public_ip(Proxy(**proxy).as_string()) if proxy else public_ip()
        for key, value in get_geolocation(geoip, geoip_db=geoip_db).as_config().items():
            if key.startswith('locale:'):
                locale_config[key] = value
            elif key == 'timezone':
                config.setdefault(key, value)
            else:
                config[key] = value
    elif (
        proxy
        and 'localhost' not in proxy.get('server', '')
        and '127.0.0.1' not in proxy.get('server', '')
        and not is_domain_set(config, 'geolocation:')
    ):
        # This is a very bad idea; the warning cannot be silenced.
        LeakWarning.warn('proxy_without_geoip')

    if locale:
        locale_config = {}
        handle_locales(locale, locale_config)
    languages = config.get('navigator.languages') or languages_for(locale_config)
    if languages:
        config['navigator.languages'] = languages

    # ---- geometry ----------------------------------------------------------
    if not _user_set_screen_window:
        coherence.repair_device_pixel_ratio(config, target_os)
        raise_screen_to_modern_floor(config)
        coherence.repair_screen_orientation(config)
        if display_bound is not None:
            # A virtual display is measured in device pixels, and the window
            # renders at the identity's ratio.
            scale = float(config.get('window.devicePixelRatio') or 1) if virtual_display else 1.0
            clamp_screen_to_display(
                config, int(display_bound.max_width / scale), int(display_bound.max_height / scale)
            )
        fix_screen_no_taskbar(config, target_os)
        clamp_window_dimensions(config)
        clamp_window_position(config)

    # ---- coherence ---------------------------------------------------------
    violations = coherence.apply(config, target_os, keep=_caller_keys)
    warn_kept_incoherent(violations)
    fatal = [v for v in violations if v.rule in coherence.FATAL_RULES]
    if fatal and not i_know_what_im_doing:
        raise IncoherentIdentity(
            'Camouwolf refuses to launch an identity that contradicts itself:\n  - '
            + '\n  - '.join(v.detail for v in fatal)
            + '\nFix `config`, or pass i_know_what_im_doing=True to launch anyway.'
        )

    validate_config(config, path=browser_binary)

    # ---- switches ----------------------------------------------------------
    if block_images:
        LeakWarning.warn('block_images', i_know_what_im_doing)
        args.append('--blink-settings=imagesEnabled=false')
    # Chromium sends WebRTC's UDP around the proxy, and STUN then reports the
    # host's public IP; with a proxy, only the proxied route is left by default.
    if block_webrtc is False and proxy:
        LeakWarning.warn('webrtc_beside_proxy', i_know_what_im_doing)
    if block_webrtc or (block_webrtc is None and proxy):
        args.append('--force-webrtc-ip-handling-policy=disable_non_proxied_udp')
    if block_webgl:
        LeakWarning.warn('block_webgl', i_know_what_im_doing)
        args.extend(['--disable-webgl', '--disable-webgl2'])
    if not enable_cache:
        args.append('--disk-cache-size=1')

    add_default_addons(addons, exclude_addons)
    if addons:
        confirm_paths(addons)
        args.append('--load-extension=' + ','.join(abspath(path) for path in addons))
        # Chrome 137+ ignores --load-extension unless this feature is off. A
        # repeated --disable-features replaces Playwright's own list (Chromium
        # reads the last one), so that list is restated with it.
        args.append(
            '--disable-features='
            + ','.join([*PLAYWRIGHT_DISABLED_FEATURES, 'DisableLoadExtensionCommandLineSwitch'])
        )

    audit = audit_switches(args, strict=not i_know_what_im_doing)
    launch_args = audit.args
    if debug:
        for arg, reason in audit.dropped:
            print(f'[camouwolf] dropped {arg}: {reason}')

    # The switches the audit refuses from a caller are the ones the launcher
    # derives from the identity, so the window, the UI language and the
    # Accept-Language header cannot disagree with it.
    outer_width, outer_height = config.get('window.outerWidth'), config.get('window.outerHeight')
    if outer_width and outer_height:
        launch_args.append(f'--window-size={int(outer_width)},{int(outer_height)}')
    screen_x, screen_y = config.get('window.screenX'), config.get('window.screenY')
    if screen_x is not None and screen_y is not None:
        launch_args.append(f'--window-position={int(screen_x)},{int(screen_y)}')
    if languages:
        launch_args.append(f'--lang={languages[0]}')
        launch_args.append(f'--accept-lang={",".join(languages)}')
        if OS_NAME == 'lin':
            # Chromium on Linux takes its UI locale from the environment.
            env['LANGUAGE'] = languages[0].replace('-', '_')
    launch_args = merge_list_switches(launch_args)

    # ---- chromium_prefs ----------------------------------------------------
    if chromium_prefs:
        user_data_dir = launch_options.get('user_data_dir')
        if not user_data_dir:
            raise ValueError(
                'chromium_prefs are written to the profile, so they need a persistent context: pass '
                'persistent_context=True and user_data_dir=. For a non-persistent launch, use `args` '
                '(command-line switches) instead.'
            )
        write_chromium_prefs(user_data_dir, chromium_prefs)

    # ---- environment -------------------------------------------------------
    if owned_display:
        dpr = float(config.get('window.devicePixelRatio') or 1)
        width = config.get('screen.width') or config.get('window.outerWidth')
        height = config.get('screen.height') or config.get('window.outerHeight')
        if width and height:
            owned_display.fit(math.ceil(width * dpr), math.ceil(height * dpr))
        env['DISPLAY'] = owned_display.get()
    if debug:
        print('[DEBUG] Config:')
        pprint(config)
    env.update(get_env_vars(config, target_os, path=browser_binary))

    ignored = launch_options.pop('ignore_default_args', None)
    if ignored is True:
        ignore_default_args: Any = True
    else:
        ignore_default_args = list(dict.fromkeys([*IGNORED_DEFAULT_ARGS, *(ignored or [])]))

    return {
        'executable_path': browser_binary,
        'args': launch_args,
        'env': env,
        'headless': headless,
        'ignore_default_args': ignore_default_args,
        **({'proxy': proxy} if proxy else {}),
        **launch_options,
    }
