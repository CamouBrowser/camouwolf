import base64
import hashlib
import ipaddress
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
from typing import Any, Callable, Dict, List, Literal, NamedTuple, Optional, Tuple, Union

import orjson
from typing_extensions import TypeAlias

from . import clienthints, coherence, driver_defaults
from ._warnings import WARNINGS_DATA, LeakWarning, _warn_from_caller
from .addons import DefaultAddons, add_default_addons, confirm_paths
from camoucore.display import has_display, largest_display
from .exceptions import (
    IncoherentIdentity,
    UnknownIPLocation,
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
    snap_window_to_device_pixels,
    window_switch_bounds,
)
from camoucore.geolocation import geoip_allowed, get_geolocation
from .gpudisplay import GPU_SWITCHES, GpuDisplay, async_prove_gpu, prove_gpu, select_gpu
from .humanize import HumanizeSetting
from .humanize import normalize as normalize_humanize
from camoucore.ip import Proxy, public_ip, public_ipv6, valid_ipv4
from camoucore.locales import handle_locales
from .product import PRODUCT
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
from .switches import (
    IGNORED_DEFAULT_ARGS,
    PLAYWRIGHT_HEADLESS_BLINK_SETTINGS,
    LAUNCHER_DISABLED_FEATURES,
    PLAYWRIGHT_DISABLED_FEATURES,
    audit_switches,
    merge_list_switches,
)
from camoucore.virtdisplay import VirtualDisplay
from .webgl import chrome_gpus, readout_names, usable_gpu

ListOrString: TypeAlias = Union[Tuple[str, ...], List[str], str]

# The schema of the config the browser reads: every key, with its type.
PROPERTIES_FILE: Path = LOCAL_DATA / 'data' / 'properties.json'
SYSTEM_COLORS: Dict[str, Dict[str, int]] = orjson.loads((LOCAL_DATA / 'data' / 'system-colors.json').read_bytes())
CHROME_LANGUAGES: Dict[str, Any] = orjson.loads((LOCAL_DATA / 'data' / 'chrome-languages.json').read_bytes())

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
        if expected_type == 'array' and not all(isinstance(item, str) for item in value):
            raise InvalidPropertyType(f'Invalid type for property {key}. Expected an array of strings.')
        if key in ('webGl:parameters', 'webGl2:parameters'):
            validate_webgl_parameters(key, value)
        if key == 'systemColors':
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in value.values()):
                raise InvalidPropertyType('Invalid systemColors: every value must be an 0xRRGGBB integer.')


def validate_webgl_parameters(key: str, parameters: Dict[str, Any]) -> None:
    """Every entry is a decimal pname and a scalar, or a list of 2 to 4 numbers
    (the typed arrays getParameter returns); the browser reads nothing else."""
    for pname, value in parameters.items():
        if not str(pname).isdigit():
            raise InvalidPropertyType(f'Invalid {key} key {pname!r}: expected a decimal GLenum.')
        is_array = isinstance(value, list) and 2 <= len(value) <= 4 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        if not is_array and not isinstance(value, (bool, int, float, str)):
            raise InvalidPropertyType(
                f'Invalid {key}[{pname}]: expected a number, boolean, string or a list of 2 to 4 numbers, '
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
        LeakWarning.warn(WARNINGS_DATA['locale'], False)
    if is_domain_set(config, 'geolocation:', 'timezone'):
        LeakWarning.warn(WARNINGS_DATA['geolocation'], False)
    if any(key.startswith(('navigator.', 'userAgentData.')) and key != 'navigator.languages' for key in config):
        LeakWarning.warn(WARNINGS_DATA['navigator'], False)
    if is_domain_set(config, 'screen.', 'window.'):
        LeakWarning.warn(WARNINGS_DATA['viewport'], False)


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
    LeakWarning.warn(WARNINGS_DATA['custom_fingerprint'], False)


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


def build_product(executable_path: Union[str, Path]) -> str:
    """What the build claims to be: 'chromium' for the public build, 'chrome'
    for one built with Chrome emulation (version.json's `product`)."""
    version_file = build_file(executable_path, 'version.json')
    if not version_file.exists():
        return 'chromium'
    return orjson.loads(version_file.read_bytes()).get('product', 'chromium')


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
    """The language list for a resolved locale: every listed locale, or Chrome's
    own default list for the primary one (data/chrome-languages.json).

    The default is the list an off-the-record profile serves whatever the user
    set, so a Playwright context and a persistent one claim the same list."""
    listed = locale_config.get('locale:all')
    if listed:
        return [tag.strip() for tag in listed.split(',') if tag.strip()]
    language, region = locale_config.get('locale:language'), locale_config.get('locale:region')
    if not language:
        return None
    primary = f'{language}-{region}' if region else language
    # language_prefs.cc GetIncognitoLanguageList: a first language the table
    # lacks is followed by the en-US list.
    return list(CHROME_LANGUAGES['lists'].get(primary) or [primary, *CHROME_LANGUAGES['lists'][CHROME_LANGUAGES['fallback']]])


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
    return geolocation_of(launch_config(from_options))


def geolocation_of(config: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The position an identity places its browser at, as Playwright's
    `geolocation` option, or None."""
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
    from camoucore.cpu_affinity import host_cores, supported

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

# Playwright context options that would make one context's pages a different
# machine from its workers and request headers, which answer from the
# context's identity whatever Playwright emulates in a page.
PER_CONTEXT_IDENTITY_OPTIONS: Dict[str, str] = {
    'user_agent': "navigator.userAgent comes from the identity; NewContext draws a context's own",
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
    """The context options in `kwargs` that would make a context's pages
    disagree with its identity, each with the reason."""
    return [
        f'{option}= cannot be set per context: {why}'
        for option, why in PER_CONTEXT_IDENTITY_OPTIONS.items()
        if kwargs.get(option) is not None
    ]


def refuse_per_context_identity(kwargs: Dict[str, Any]) -> None:
    """Raise IncoherentIdentity for a context option that would make a
    context's pages disagree with its identity."""
    problems = per_context_identity_problems(kwargs)
    if problems:
        raise IncoherentIdentity(
            '; '.join(problems)
            + ". Workers and request headers would still answer as the context's identity. "
            'NewContext draws a context its own.'
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


_LAUNCH_OPTIONS = '_camouwolf_launch_options'

# Patch 0031 sends a proxied context's WebRTC through the proxy; this keeps it to
# TCP there, with no UDP at all.
WEBRTC_BLOCK_SWITCH = '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'

# Camoufox's NewContext options that would name another browser build than the
# one running, which every context shares.
OTHER_BUILD_OPTIONS = ('preset', 'ff_version', 'chrome_version')


def keeps_ipv6(geo_config: Dict[str, Any], geoip_db: Optional[str], i_know_what_im_doing: Optional[bool]) -> bool:
    """Whether this host has a public IPv6 address that geolocates where the
    IPv4 identity does. Warns when it has one that does not."""
    ipv6 = public_ipv6()
    if ipv6 is None:
        return False
    try:
        ipv6_config = get_geolocation(ipv6, geoip_db=geoip_db).as_config()
    except UnknownIPLocation:
        ipv6_config = {}
    if all(ipv6_config.get(key) == geo_config.get(key) for key in ('timezone', 'locale:region')):
        return True
    LeakWarning.warn(WARNINGS_DATA['ipv6_elsewhere'], i_know_what_im_doing)
    return False


def refuse_context_options(context_kwargs: Dict[str, Any]) -> None:
    """Raise for a NewContext option a context cannot take: NotImplementedError
    for a Camoufox one naming another browser build than the one every context
    runs in, IncoherentIdentity for one its pages would contradict."""
    for option in OTHER_BUILD_OPTIONS:
        if context_kwargs.get(option) is not None:
            raise NotImplementedError(
                f'NewContext({option}=) is not implemented in Camouwolf: a context runs in the '
                "browser's own Chromium build. Launch another browser for another build."
            )
    refuse_per_context_identity(context_kwargs)


def launch_languages(from_options: Dict[str, Any]) -> Optional[List[str]]:
    """The language list these launch options give the browser."""
    for arg in from_options.get('args') or []:
        if str(arg).startswith('--accept-lang='):
            return str(arg).split('=', 1)[1].split(',')
    return None


def launch_full_version(from_options: Dict[str, Any]) -> str:
    """The Chromium version the launch's identity claims: the binary's, or the
    chrome_version milestone the launch was given."""
    full_version = resolve_version(from_options.get('executable_path'))
    claimed = re.search(r'Chrome/(\d+)', launch_config(from_options).get('navigator.userAgent') or '')
    if claimed and int(claimed[1]) != int(full_version.split('.', 1)[0]):
        return f'{int(claimed[1])}.0.0.0'
    return full_version


# Keys of the browser rather than of a machine, which a context keeps from the
# launch: its renderers read the context's config in place of the launch's.
BROWSER_KEYS = ('network:ipv4Only', 'allowAddonNewtab', 'instantAnimations', 'showcursor')


def context_identity(
    from_options: Dict[str, Any],
    *,
    proxy: Optional[Dict[str, str]] = None,
    os: Optional[str] = None,
    fingerprint: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    webgl_config: Optional[Tuple[str, str]] = None,
    webrtc_ip: Optional[str] = None,
    fonts: Optional[List[str]] = None,
    custom_fonts_only: Optional[bool] = None,
    locale: Optional[Union[str, List[str]]] = None,
    timezone_id: Optional[str] = None,
    i_know_what_im_doing: Optional[bool] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """A context's own identity and language list, drawn for the browser that
    `from_options` launched.

    The machine is drawn afresh on the launch's OS, screen, window and core
    count, which one browser process cannot vary. A proxied context is placed
    where the proxy exits, looked up through it; any other leaves from the
    browser's address and so keeps the launch's place and languages.
    """
    launch = launch_config(from_options)
    if not launch:
        raise ValueError('NewContext needs a browser launched by Camouwolf: a context identity is drawn to fit it')
    target_os = launch_os(from_options)
    if os is not None and os != target_os:
        raise ValueError(
            f'NewContext(os={os!r}): the browser launched as {target_os!r}, and its contexts share the '
            'fonts and maths library it launched with. Launch another browser for another OS.'
        )
    config = dict(config or {})
    if not i_know_what_im_doing:
        warn_manual_config(config)
    if timezone_id:
        config['timezone'] = timezone_id
    if webrtc_ip:
        config['webrtc:ipv4' if valid_ipv4(webrtc_ip) else 'webrtc:ipv6'] = webrtc_ip
    if not proxy:
        for key, value in launch.items():
            if key == 'timezone' or key.startswith(('geolocation:', 'webrtc:')):
                config.setdefault(key, value)
        locale = locale or launch_languages(from_options)
    identity, languages, _ = draw_identity(
        config,
        os=target_os,
        webgl_config=webgl_config,
        geoip=bool(proxy),
        geoip_db=None,
        proxy=proxy,
        block_webrtc=WEBRTC_BLOCK_SWITCH in (from_options.get('args') or []),
        locale=locale,
        fingerprint=fingerprint,
        screen=None,
        window=None,
        fonts=fonts,
        custom_fonts_only=custom_fonts_only,
        canvas_noise=bool(launch.get('canvas:randomizeReadback')),
        pin_cpu_cores=None,
        full_version=launch_full_version(from_options),
        # The launch's OS already passed the policy.
        allow_os_mismatch=True,
        i_know_what_im_doing=i_know_what_im_doing,
        browser_binary=from_options['executable_path'],
        launch=launch,
    )
    identity.update({k: launch[k] for k in BROWSER_KEYS if k in launch})
    # The browser serves navigator.languages from the context's language pref.
    identity.pop('navigator.languages', None)
    if not languages:
        raise ValueError('NewContext needs a locale: neither it nor the launch names one')
    return identity, languages


def new_context_options(
    context_kwargs: Dict[str, Any],
    identity: Dict[str, Any],
    proxy: Optional[Dict[str, str]] = None,
    geolocation: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """The new_context() options NewContext passes for a context with
    `identity`: the caller's, placed where the identity is."""
    storage_state = context_kwargs.get('storage_state')
    if isinstance(storage_state, (str, Path)):
        storage_state = orjson.loads(Path(storage_state).read_bytes())
    if storage_state and storage_state.get('origins'):
        # Playwright writes them from a page, and a context takes its identity
        # before its first renderer.
        raise ValueError(
            "NewContext(storage_state=) with origins' localStorage is not supported: set it from a "
            'page of the new context instead. Cookies alone are.'
        )
    options = dict(context_kwargs)
    if proxy:
        options['proxy'] = proxy
    geolocation = geolocation or geolocation_of(identity)
    if geolocation:
        options['geolocation'] = geolocation
        permissions = list(options.get('permissions') or [])
        if 'geolocation' not in permissions:
            permissions.append('geolocation')
        options['permissions'] = permissions
    return options


def set_context_identity_params(context_id: str, identity: Dict[str, Any], languages: List[str]) -> Dict[str, Any]:
    """Target.setBrowserContextIdentity's parameters (patch 0034)."""
    return {
        'browserContextId': context_id,
        'identity': orjson.dumps(identity).decode('utf-8'),
        'languages': languages,
    }


def created_context_id(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """The one browser context Target.getBrowserContexts lists in `after` and
    not in `before`."""
    new = set(after['browserContextIds']) - set(before['browserContextIds'])
    if len(new) != 1:
        raise RuntimeError(f'Expected one new browser context, found {len(new)}: was another made at the same time?')
    return new.pop()


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
    """Give every new_page()/new_context() of `browser` Camouwolf's defaults,
    and keep the launch options NewContext draws a context's identity to fit."""
    _wrap_context_factories(browser, context_defaults(launch_geolocation(from_options)))
    driver_defaults.mark(browser)
    setattr(getattr(browser, '_impl_obj', browser), _LAUNCH_OPTIONS, from_options)


def launch_options_of(browser: Any) -> Dict[str, Any]:
    """The launch options a Camouwolf browser was launched with."""
    from_options = getattr(getattr(browser, '_impl_obj', browser), _LAUNCH_OPTIONS, None)
    if from_options is None:
        raise ValueError('NewContext needs a browser from NewBrowser or Camouwolf: a context identity is drawn to fit it')
    return from_options


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
        display = VirtualDisplay(PRODUCT, debug=bool(debug))
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


class PresetIdentity(NamedTuple):
    os: str
    webgl_config: Tuple[str, str]
    config: Dict[str, Any]


# A preset's fields and the config keys they set; the rest of the identity is
# drawn from an fpgen record of a machine with the preset's GPU.
_PRESET_KEYS = {
    'navigator': {'hardwareConcurrency': 'navigator.hardwareConcurrency', 'maxTouchPoints': 'navigator.maxTouchPoints'},
    'screen': {
        'width': 'screen.width', 'height': 'screen.height', 'colorDepth': 'screen.colorDepth',
        'availWidth': 'screen.availWidth', 'availHeight': 'screen.availHeight',
        'devicePixelRatio': 'window.devicePixelRatio',
    },
}


def preset_identity(preset: Dict[str, Any]) -> PresetIdentity:
    """The OS, GPU and recorded values of a Camoufox-format preset recorded
    from Chrome. Its user agent names only the OS: the version is the binary's."""
    if not isinstance(preset, dict):
        raise ValueError(f'fingerprint_preset must be a dict, not {type(preset).__name__}')
    user_agent = str((preset.get('navigator') or {}).get('userAgent') or '')
    if 'Chrome/' not in user_agent or 'Firefox/' in user_agent:
        raise NonChromeFingerprint(
            f'fingerprint_preset was not recorded from Chrome (its user agent is {user_agent!r}); '
            'a Firefox preset describes a browser Camouwolf is not'
        )
    target_os = clienthints.ua_os(user_agent)
    if target_os is None:
        raise ValueError(f'fingerprint_preset user agent {user_agent!r} names no desktop OS')
    webgl = preset.get('webgl') or {}
    if not (webgl.get('unmaskedVendor') and webgl.get('unmaskedRenderer')):
        raise ValueError('fingerprint_preset needs webgl.unmaskedVendor and webgl.unmaskedRenderer')
    config = {
        key: preset[section][field]
        for section, fields in _PRESET_KEYS.items()
        for field, key in fields.items()
        if preset.get(section, {}).get(field) is not None
    }
    if preset.get('timezone'):
        config['timezone'] = preset['timezone']
    return PresetIdentity(OS_NAMES[target_os], (webgl['unmaskedVendor'], webgl['unmaskedRenderer']), config)


def oses_for_cpu(cpu: Any, os: ListOrString) -> ListOrString:
    """The OSes in `os` that have `cpu`; ValueError when none does."""
    names = [os] if isinstance(os, str) else list(os)
    fits, errors = [], []
    for name in names:
        try:
            coherence.resolve_cpu(cpu, OS_KEYS.get(name, name))
            fits.append(name)
        except ValueError as e:
            errors.append(str(e))
    if not fits:
        raise ValueError(f'cpu={cpu!r}: ' + '; '.join(errors))
    return os if isinstance(os, str) else fits


SOCKS5_DEFAULT_PORT = 1080


def socks5_proxy_key(server: str) -> str:
    """The "host:port" the browser looks a SOCKS5 proxy's credentials up by:
    the proxy's canonical host and port, as net::HostPortPair prints them."""
    _, host, port = Proxy.parse_server(server)
    host = host.strip('[]').lower()
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    host = f'[{host}]' if ':' in host else host
    return f'{host}:{port or SOCKS5_DEFAULT_PORT}'


def split_socks5_credentials(
    proxy: Optional[Dict[str, str]],
) -> Tuple[Optional[Dict[str, str]], Optional[Dict[str, Dict[str, str]]]]:
    """A socks5 proxy without its credentials, for Playwright (which refuses
    them for Chromium), and the credentials as proxy:socks5Credentials, which
    the browser reads from its config rather than from argv."""
    if not proxy or not proxy.get('username') or Proxy.parse_server(proxy['server'])[0] != 'socks5':
        return proxy, None
    username, password = proxy['username'], proxy.get('password') or ''
    for field, value in (('username', username), ('password', password)):
        if not 1 <= len(value.encode()) <= 255:
            raise ValueError(f'A SOCKS5 {field} is 1 to 255 bytes (RFC 1929)')
    stripped = {k: v for k, v in proxy.items() if k not in ('username', 'password')}
    return stripped, {socks5_proxy_key(proxy['server']): {'username': username, 'password': password}}


def masked_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """The config with proxy credentials masked, for printing."""
    credentials = config.get('proxy:socks5Credentials')
    if not credentials:
        return config
    return {
        **config,
        'proxy:socks5Credentials': {key: {'username': entry['username'], 'password': '***'}
                                    for key, entry in credentials.items()},
    }


_PEM_CERTIFICATE = re.compile(r'-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----', re.S)


def read_certificate_paths(config: Dict[str, Any]) -> None:
    """Move certificatePaths into certificates: each file's certificates, PEM
    or DER, as the base64 DER the browser trusts."""
    paths = config.pop('certificatePaths', None) or []
    certificates = [_PEM_CERTIFICATE.sub(lambda m: m[1], c) for c in config.get('certificates') or []]
    for path in paths:
        data = Path(path).read_bytes()
        bodies = _PEM_CERTIFICATE.findall(data.decode('latin-1'))
        certificates.extend(bodies or [base64.b64encode(data).decode()])
    if certificates:
        config['certificates'] = [re.sub(r'\s+', '', c) for c in certificates]


def speaks(language: str) -> Callable[[Any], bool]:
    """An fpgen condition on navigator.languages: the first is `language`'s."""
    wanted = language.split('-')[0].lower()

    def condition(value: Any) -> bool:
        return isinstance(value, (list, tuple)) and bool(value) and str(value[0]).split('-')[0].lower() == wanted

    return condition


def draw_fingerprint(
    os: Optional[ListOrString],
    screen: Optional[Screen],
    window: Optional[Tuple[int, int]],
    webgl_config: Optional[Tuple[str, str]],
    language: Optional[str] = None,
    cpu: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Draw an fpgen Chrome fingerprint whose GPU the identity may claim.

    With `cpu`, a record of a machine that has that CPU (see launch_options).

    With `language`, a record of Chrome in that language where fpgen has one:
    a machine's voices and fonts follow the language its OS was installed in.

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

    if cpu is not None:
        claims = [(OS_KEYS[name], coherence.resolve_cpu(cpu, OS_KEYS[name])) for name in names]
        if webgl_config:
            misfits = [coherence.cpu_misfit(webgl_config[1], None, os_key, *claim) for os_key, claim in claims]
            if all(misfits):
                raise ValueError(f'cpu={cpu!r}: no machine has {misfits[0]}')
        any_gpu = gpu

        def gpu(value: Any) -> bool:
            return any_gpu(value) and any(
                not coherence.cpu_misfit(value.get('renderer'), None, os_key, *claim) for os_key, claim in claims
            )

    conditions: Dict[str, Any] = {'gpu': gpu}
    if language:
        conditions['languages'] = speaks(language)
    for _ in range(MAX_FINGERPRINT_DRAWS):
        try:
            fingerprint = generate_fingerprint(window=window, screen=screen, os=os or None, **conditions)
        except (ValueError, StopIteration):
            if 'languages' not in conditions:
                raise
            # fpgen has no record of Chrome in this language on these machines
            # (its sampler raises StopIteration on a pairing it never recorded).
            # The draw is then a machine installed in another language.
            del conditions['languages']
            continue
        drawn_os = fingerprint_os(fingerprint)
        # Camouwolf builds are x86-64 desktops. fpgen also records Chrome on ARM
        # Linux ("Linux armv81", "Linux aarch64"), whose platform a build that
        # computes as x86-64 cannot back up.
        platform = (fingerprint.get('navigator') or {}).get('platform')
        if platform != clienthints.NAV_PLATFORM.get(drawn_os):
            continue
        if not usable_gpu(fingerprint.get('gpu'), drawn_os):
            continue
        if not readout_names(fingerprint, fingerprint['gpu']['renderer']):
            continue
        if coherence.machine_misfit(
            fingerprint['gpu']['renderer'], drawn_os, (fingerprint.get('navigator') or {}).get('hardwareConcurrency'),
            (fingerprint.get('screen') or {}).get('width'), (fingerprint.get('screen') or {}).get('height'),
        ):
            continue
        if cpu is not None and coherence.cpu_misfit(
            fingerprint['gpu']['renderer'], (fingerprint.get('navigator') or {}).get('hardwareConcurrency'),
            drawn_os, *coherence.resolve_cpu(cpu, drawn_os),
        ):
            continue
        return fingerprint
    raise RuntimeError(
        f'fpgen drew no desktop Chrome fingerprint with a usable GPU for os={os!r} in '
        f'{MAX_FINGERPRINT_DRAWS} draws'
    )


# The keys a context's identity takes from the launch's. The window is one
# window and its pixel ratio the display's, whichever context draws in it.
LAUNCH_GEOMETRY = ('screen.', 'window.')


def draw_identity(
    config: Dict[str, Any],
    *,
    os: Optional[ListOrString],
    webgl_config: Optional[Tuple[str, str]],
    geoip: Optional[Union[str, bool]],
    geoip_db: Optional[str],
    proxy: Optional[Dict[str, str]],
    block_webrtc: Optional[bool],
    locale: Optional[Union[str, List[str]]],
    fingerprint: Optional[Dict[str, Any]],
    screen: Optional[Screen],
    window: Optional[Tuple[int, int]],
    fonts: Optional[List[str]],
    custom_fonts_only: Optional[bool],
    canvas_noise: Optional[bool],
    pin_cpu_cores: Optional[bool],
    full_version: str,
    allow_os_mismatch: Optional[bool],
    i_know_what_im_doing: Optional[bool],
    browser_binary: str,
    display_bound: Optional[Screen] = None,
    virtual_display: bool = False,
    launch: Optional[Dict[str, Any]] = None,
    cpu: Optional[Dict[str, str]] = None,
    recorded: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Optional[List[str]], str]:
    """A whole identity: the config, its language list, and its OS.

    `config` holds the caller's keys, which win over drawn ones. With `launch`
    (the browser's own config) it is a context's identity: the screen, window
    and core count are the launch's, which one browser process cannot vary.
    `recorded` holds a preset's values, which stand as a caller's would except
    the core count.
    """
    _caller_keys = frozenset(config)
    _user_set_screen_window = is_domain_set(config, 'screen.', 'window.')
    milestone = int(full_version.split('.', 1)[0])

    # ---- place: geoip, locale ---------------------------------------------
    locale_config: Dict[str, Any] = {}
    if geoip:
        geoip_allowed()  # Assert that geoip is allowed
        if geoip is True:
            geoip = public_ip(Proxy(**proxy).as_string()) if proxy else public_ip()
        geo_config = get_geolocation(geoip, geoip_db=geoip_db).as_config()
        for key, value in geo_config.items():
            if key.startswith('locale:'):
                locale_config[key] = value
            elif key == 'timezone':
                config.setdefault(key, value)
            else:
                config[key] = value
        # Without a proxy, a site that has an IPv6 address sees this host's,
        # not the IPv4 address the identity was drawn from. Keep IPv6 only when
        # it places the host where the identity does.
        if (
            launch is None
            and not proxy
            and valid_ipv4(geoip)
            and not keeps_ipv6(geo_config, geoip_db, i_know_what_im_doing)
        ):
            config['network:ipv4Only'] = True
        # Behind a proxy, WebRTC sees the network of a machine at the exit
        # (patch 0031): its one adapter has the exit's address.
        if proxy and not block_webrtc:
            config.setdefault('webrtc:ipv4' if valid_ipv4(geoip) else 'webrtc:ipv6', geoip)
    elif (
        proxy
        and 'localhost' not in proxy.get('server', '')
        and '127.0.0.1' not in proxy.get('server', '')
        and not is_domain_set(config, 'geolocation:')
    ):
        # This is a very bad idea; the warning cannot be silenced.
        LeakWarning.warn(WARNINGS_DATA['proxy_without_geoip'])

    if locale:
        locale_config = {}
        handle_locales(locale, locale_config)
    # Chrome serves navigator.languages and Accept-Language from one pref, which
    # --accept-lang sets, so the config does not repeat it: an off-the-record
    # context swaps the pref's list, and a pinned copy would then disagree.
    languages = config.get('navigator.languages') or languages_for(locale_config)

    # A caller who names the user agent themselves has pinned the identity.
    if launch is not None:
        # The window, and so the screen it sits on, is the browser's: a
        # context's machine is drawn with the launch's screen.
        screen = Screen(
            min_width=launch.get('screen.width'), max_width=launch.get('screen.width'),
            min_height=launch.get('screen.height'), max_height=launch.get('screen.height'),
        )
        window = None
    if fingerprint is None and 'navigator.userAgent' not in config:
        fingerprint = draw_fingerprint(
            os, screen or display_bound, window, webgl_config, languages[0] if languages else None, cpu
        )
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
        merge_into(
            config,
            from_fpgen(
                fingerprint, chrome_version=milestone, network_voices=build_product(browser_binary) == 'chrome'
            ),
        )
    if recorded:
        recorded = {k: v for k, v in recorded.items() if k not in _caller_keys}
        config.update(recorded)
        _caller_keys |= set(recorded) - {'navigator.hardwareConcurrency'}
        _user_set_screen_window = _user_set_screen_window or is_domain_set(recorded, 'screen.', 'window.')
    # Patch 0012 shows the host's voices only when the key is absent, so it is
    # always sent: the recorded list, or none, never the host's.
    config.setdefault('voices', [])

    target_os = get_target_os(config)
    check_os_policy([OS_NAMES[target_os]], allow_os_mismatch)

    config.setdefault(
        'navigator.userAgent',
        clienthints.build_user_agent(
            clienthints.ChromeIdentity(milestone=milestone, full_version=full_version, os=target_os)
        ),
    )
    config.setdefault('navigator.platform', clienthints.NAV_PLATFORM[target_os])
    cpu_claim = coherence.resolve_cpu(cpu, target_os) if cpu is not None else None
    if cpu_claim:
        config.setdefault('userAgentData.architecture', cpu_claim[0])
    clienthints.fill_client_hints(config, target_os, full_version)

    # The core count a page can measure is the host's, unless the browser is
    # pinned to the drawn one.
    if launch is not None:
        # Measurable parallelism is the process's, pinned or not.
        config['navigator.hardwareConcurrency'] = launch.get('navigator.hardwareConcurrency')
    elif 'navigator.hardwareConcurrency' not in _caller_keys:
        fix_hardware_concurrency(config, can_pin=bool(pin_cpu_cores))
    if cpu_claim:
        misfit = coherence.cpu_misfit(
            config.get('webGl:renderer'), config.get('navigator.hardwareConcurrency'), target_os, *cpu_claim
        )
        if misfit:
            raise ValueError(f'cpu={cpu!r}: no machine has {misfit}')

    # ---- fonts -------------------------------------------------------------
    if custom_fonts_only:
        if not fonts:
            raise ValueError('custom_fonts_only=True needs `fonts`')
        LeakWarning.warn(WARNINGS_DATA['custom_fonts_only'], i_know_what_im_doing)
        config['fonts'] = list(dict.fromkeys(fonts))
    elif fonts:
        base = config.get('fonts') or ESSENTIAL_FONTS[target_os]
        config['fonts'] = list(dict.fromkeys([*base, *fonts]))

    # ---- devices, keyboard, colours ---------------------------------------
    config.setdefault('mediaDevices:enumerated', list(DEFAULT_MEDIA_DEVICES[target_os]))
    # A desktop on mains power, as Chrome reports one with no battery. A frozen
    # discharging level would be the tell; a caller can still claim one.
    config.setdefault('battery:charging', True)
    config.setdefault('battery:level', 1)
    layout = keyboard_layout_map(target_os)
    if layout:
        config.setdefault('keyboard.layoutMap', layout)
    colors = SYSTEM_COLORS.get(OS_NAMES[target_os])
    if colors:
        config.setdefault('systemColors', dict(colors))

    # ---- canvas ------------------------------------------------------------
    if canvas_noise:
        LeakWarning.warn(WARNINGS_DATA['canvas_noise'], i_know_what_im_doing)
        config.setdefault('canvas:seed', secrets.randbelow(2**32 - 1) + 1)
        config.setdefault('canvas:randomizeReadback', True)
    else:
        # Stock Chrome returns the pixels it drew.
        config.setdefault('canvas:seed', 0)
        config.setdefault('canvas:randomizeReadback', False)
    config.setdefault('canvas:quantizeTextMetrics', False)
    config.setdefault('AudioContext:randomizeSamples', False)

    # ---- geometry ----------------------------------------------------------
    if launch is not None:
        for key in [k for k in config if k.startswith(LAUNCH_GEOMETRY)]:
            del config[key]
        config.update({k: v for k, v in launch.items() if k.startswith(LAUNCH_GEOMETRY)})
    elif not _user_set_screen_window:
        coherence.repair_device_pixel_ratio(
            config, target_os, keep=('window.outerWidth', 'window.outerHeight') if window else ()
        )
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
        snap_window_to_device_pixels(config)

    # ---- coherence ---------------------------------------------------------
    kept = _caller_keys
    if launch is not None:
        kept |= {k for k in config if k.startswith(LAUNCH_GEOMETRY)} | {'navigator.hardwareConcurrency'}
    violations = coherence.apply(config, target_os, keep=kept)
    warn_kept_incoherent(violations)
    fatal = [v for v in violations if v.rule in coherence.FATAL_RULES]
    if fatal and not i_know_what_im_doing:
        raise IncoherentIdentity(
            'Camouwolf refuses to launch an identity that contradicts itself:\n  - '
            + '\n  - '.join(v.detail for v in fatal)
            + '\nFix `config`, or pass i_know_what_im_doing=True to launch anyway.'
        )

    validate_config(config, path=browser_binary)

    return config, languages, target_os


def launch_options(
    *,
    config: Optional[Dict[str, Any]] = None,
    os: Optional[ListOrString] = None,
    block_images: Optional[bool] = None,
    block_webrtc: Optional[bool] = None,
    block_webgl: Optional[bool] = None,
    disable_coop: Optional[bool] = None,
    instant_animations: Optional[bool] = None,
    show_cursor: Optional[bool] = None,
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
            Keep WebRTC to TCP through the proxy, with no UDP. Without it, a
            proxied launch's WebRTC is a machine's at the proxy's exit: UDP
            goes through a SOCKS5 proxy's relay, and through no other proxy.
        block_webgl (Optional[bool]):
            Whether to block WebGL. To prevent leaks, only use this for special cases.
        disable_coop (Optional[bool]):
            Ignore Cross-Origin-Opener-Policy headers, so a popup a COOP page
            opens keeps its opener. A COOP page is then not crossOriginIsolated,
            which it can read, so it warns.
        instant_animations (Optional[bool]):
            Finish every finite CSS and Web Animation at once. A page reads the
            zero duration back, so it warns.
        show_cursor (Optional[bool]):
            Show where automation clicks, with DevTools' click visualization:
            a ripple at each mouse or pointer press, painted in the overlay a
            page cannot read. Screenshots and a watched window show it.
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
            of them are navigator.languages and the Accept-Language header. One
            locale claims Chrome's default list for it ("de-DE" is de-DE, de,
            en-US, en). A non-persistent context serves that default for the
            first of a longer list, as Chrome does off the record.
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
            A device recorded from real Chrome, in Camoufox's preset format:
            {"navigator": {"userAgent", "hardwareConcurrency", "maxTouchPoints"},
            "screen": {"width", "height", "colorDepth", "availWidth",
            "availHeight", "devicePixelRatio"}, "webgl": {"unmaskedVendor",
            "unmaskedRenderer"}, "timezone"}. The rest is drawn from an fpgen
            record of a machine with that GPU, and the version is the binary's.
            True raises ValueError: no Chrome presets are bundled.
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
            Let extensions open tabs and windows of their own (chrome.tabs.create,
            chrome.windows.create, an options page). Off by default, so
            automation sees only the pages it opened.
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
            A socks5:// proxy may carry a username and password: the browser
            answers the proxy's RFC 1929 challenge with them. They travel in
            the config's environment variables, never on the command line.
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
            Prints the config being sent to Camouwolf, with proxy credentials
            masked, and has the browser log to stderr (--enable-logging=stderr).
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
            The CPU the identity claims: {'arch': 'x86' or 'arm', 'manufacturer':
            ...}, either key optional (a manufacturer implies its arch). It is
            Sec-CH-UA-Arch, and the GPU is drawn from machines with that CPU.
            Manufacturers: windows x86 intel or amd, arm qualcomm; macos x86
            intel, arm apple; linux x86 intel or amd. The browser still computes
            as the host's x86-64 CPU does.
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
    if fingerprint_preset is True:
        raise ValueError(
            'fingerprint_preset=True picks a bundled preset, and Camouwolf bundles none: Camoufox\'s '
            'presets are recorded from Firefox. Pass a preset recorded from Chrome as a dict, or leave '
            'it unset to draw an fpgen fingerprint.'
        )
    preset = preset_identity(fingerprint_preset) if fingerprint_preset else None
    # main_world_eval: nothing to switch on. See the docstring.

    # The launch fills these in, so it works on copies: a caller's mapping
    # reused for a second launch would otherwise carry the first identity's
    # values in, as if the caller had set them.
    config = dict(config or {})
    addons = list(addons or [])
    args = list(args or [])
    browser_proxy, socks5_credentials = split_socks5_credentials(proxy)
    read_certificate_paths(config)

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
    if preset:
        if os or webgl_config or fingerprint is not None:
            raise ValueError('fingerprint_preset names the OS and GPU; it cannot be combined with os, '
                             'webgl_config or fingerprint')
        os, webgl_config = preset.os, preset.webgl_config
    if cpu is not None:
        os = oses_for_cpu(cpu, os or list(ALLOWED_OS_TARGETS[OS_NAME]))
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
        LeakWarning.warn(WARNINGS_DATA['chrome_version'], i_know_what_im_doing)
        full_version = f'{int(chrome_version)}.0.0.0'

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

    config, languages, target_os = draw_identity(
        config,
        os=os,
        webgl_config=webgl_config,
        geoip=geoip,
        geoip_db=geoip_db,
        proxy=proxy,
        block_webrtc=block_webrtc,
        locale=locale,
        fingerprint=fingerprint,
        screen=screen,
        window=window,
        fonts=fonts,
        custom_fonts_only=custom_fonts_only,
        canvas_noise=canvas_noise,
        pin_cpu_cores=pin_cpu_cores,
        full_version=full_version,
        allow_os_mismatch=allow_os_mismatch,
        i_know_what_im_doing=i_know_what_im_doing,
        browser_binary=browser_binary,
        display_bound=display_bound,
        virtual_display=bool(virtual_display),
        cpu=cpu,
        recorded=preset.config if preset else None,
    )

    # ---- browser behaviour ------------------------------------------------
    config.setdefault('allowAddonNewtab', bool(allow_addon_new_tab))
    if instant_animations:
        LeakWarning.warn(WARNINGS_DATA['instant_animations'], i_know_what_im_doing)
        config['instantAnimations'] = True
    if show_cursor:
        config['showcursor'] = True
    if socks5_credentials:
        config['proxy:socks5Credentials'] = socks5_credentials

    # ---- switches ----------------------------------------------------------
    if block_images:
        LeakWarning.warn(WARNINGS_DATA['block_images'], i_know_what_im_doing)
        args.append('--blink-settings=imagesEnabled=false')
    # A proxied context's WebRTC already leaves through the proxy (patch 0031);
    # this keeps it to TCP through the proxy, with no UDP at all.
    if block_webrtc:
        args.append(WEBRTC_BLOCK_SWITCH)
    if block_webgl:
        LeakWarning.warn(WARNINGS_DATA['block_webgl'], i_know_what_im_doing)
        args.extend(['--disable-webgl', '--disable-webgl2'])
    if not enable_cache:
        args.append('--disk-cache-size=1')

    add_default_addons(addons, exclude_addons)
    if addons:
        confirm_paths(addons)
        args.append('--load-extension=' + ','.join(abspath(path) for path in addons))
    # A repeated --disable-features replaces Playwright's own list (Chromium
    # reads the last one), so that list is restated with the launcher's.
    disabled = [*PLAYWRIGHT_DISABLED_FEATURES, *LAUNCHER_DISABLED_FEATURES]
    if disable_coop:
        LeakWarning.warn(WARNINGS_DATA['disable_coop'], i_know_what_im_doing)
        disabled.append('CrossOriginOpenerPolicy')
    if debug:
        args.append('--enable-logging=stderr')
    if addons:
        # Chrome 137+ ignores --load-extension unless this feature is off.
        disabled.append('DisableLoadExtensionCommandLineSwitch')
    args.append('--disable-features=' + ','.join(disabled))

    audit = audit_switches(args, strict=not i_know_what_im_doing)
    launch_args = audit.args
    if debug:
        for arg, reason in audit.dropped:
            print(f'[camouwolf] dropped {arg}: {reason}')

    # The switches the audit refuses from a caller are the ones the launcher
    # derives from the identity, so the window, the UI language and the
    # Accept-Language header cannot disagree with it.
    # Chrome rounds the window through device pixels, so the switches carry the
    # input it reads back as the claimed window.
    dpr = float(config.get('window.devicePixelRatio') or 1)
    outer_width, outer_height = config.get('window.outerWidth'), config.get('window.outerHeight')
    screen_x, screen_y = config.get('window.screenX'), config.get('window.screenY')
    if outer_width and outer_height:
        (x, width), (y, height) = (
            window_switch_bounds(int(screen_x or 0), int(outer_width), dpr),
            window_switch_bounds(int(screen_y or 0), int(outer_height), dpr),
        )
        launch_args.append(f'--window-size={width},{height}')
        if screen_x is not None and screen_y is not None:
            launch_args.append(f'--window-position={x},{y}')
    elif screen_x is not None and screen_y is not None:
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
        pprint(masked_config(config))
    env.update(get_env_vars(config, target_os, path=browser_binary))

    ignored = launch_options.pop('ignore_default_args', None)
    if ignored is True:
        ignore_default_args: Any = True
    else:
        ignore_default_args = list(dict.fromkeys([
            *IGNORED_DEFAULT_ARGS, *([PLAYWRIGHT_HEADLESS_BLINK_SETTINGS] if headless is True else []), *(ignored or [])
        ]))

    return {
        'executable_path': browser_binary,
        'args': launch_args,
        'env': env,
        'headless': headless,
        'ignore_default_args': ignore_default_args,
        **({'proxy': browser_proxy} if browser_proxy else {}),
        **launch_options,
    }
