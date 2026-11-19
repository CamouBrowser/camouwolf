"""Record tests/golden/launch.json: launch_options() of the Python launcher
over fixed inputs, which tests/launch-golden.test.ts replays through the
TypeScript launchOptions().

    ~/.cache/camouwolf-venv/bin/python scripts/golden/launch_golden.py
    (or: pnpm golden; CAMOUWOLF_PYTHON names another interpreter)

Every draw is seeded (`random.seed`), and everything launch_options() reads
from the host is replaced by the fixed values of HOST below; the TypeScript
test (tests/launch-host.ts) installs the same ones. With --check the file is
not written: the script exits 1 when the committed goldens differ from what
this checkout's Python launcher produces.
"""

import json
import os
import random
import sys
import tempfile
import warnings
from pathlib import Path

HERE = Path(__file__).resolve()
TYPESCRIPT = HERE.parents[2]
sys.path.insert(0, str(TYPESCRIPT.parent / 'python' / 'src'))

for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'CAMOUWOLF_EXECUTABLE_PATH', 'CAMOUWOLF_GPU'):
    os.environ.pop(name, None)

from camoucore import cpu_affinity  # noqa: E402
from camouwolf import DefaultAddons, addons, fingerprints, utils  # noqa: E402
from camouwolf.fingerprints import Screen  # noqa: E402
from camoucore.locales import Geolocation, Locale  # noqa: E402

FIXTURES = TYPESCRIPT / 'tests' / 'fixtures' / 'launch'
BUNDLE = FIXTURES / 'bundle'
GOLDEN = TYPESCRIPT / 'tests' / 'golden' / 'launch.json'

# The host every case runs on. tests/launch-host.ts holds the same values.
HOST = {
    'cpuCount': 16,
    'canPin': True,
    'display': [1920, 1080],
    'publicIp': '203.0.113.7',
    'geolocation': {
        'language': 'de', 'region': 'DE', 'script': 'Latn',
        'longitude': 13.4105, 'latitude': 52.5244, 'timezone': 'Europe/Berlin', 'accuracy': 25.5,
    },
    'canvasSeed': 123456789,
}

ENV = {'HOME': '/home/golden', 'PATH': '/usr/bin:/bin'}
PINNED_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/152.0.0.0 Safari/537.36'
)

# name, seed, options. `host` (default 'lin') and `hasDisplay` (default false)
# describe the machine; everything else is a launch_options() keyword.
CASES = [
    ('linux', 1, {'os': 'linux'}),
    ('windows', 2, {'os': 'windows'}),
    ('default-os', 3, {}),
    ('os-list', 4, {'os': ['windows', 'linux']}),
    ('windows-host', 5, {'host': 'win', 'os': 'windows'}),
    ('windows-host-default', 6, {'host': 'win'}),
    ('locale', 7, {'os': 'windows', 'locale': 'fr-FR'}),
    ('locale-list', 8, {'os': 'linux', 'locale': ['en-GB', 'de-DE', 'ja']}),
    ('geoip-ip', 9, {'os': 'windows', 'geoip': '198.51.100.4'}),
    ('geoip-proxy', 10, {
        'os': 'linux', 'geoip': True,
        'proxy': {'server': 'http://proxy.example:8080', 'username': 'user name', 'password': 'p@ss/w#rd'},
    }),
    ('geoip-and-locale', 11, {'os': 'windows', 'geoip': '198.51.100.4', 'locale': 'en-US'}),
    ('proxy-without-geoip', 12, {'os': 'linux', 'proxy': {'server': 'socks5://proxy.example:1080'}}),
    ('proxy-localhost', 13, {'os': 'linux', 'proxy': {'server': 'http://127.0.0.1:3128'}}),
    ('screen-max', 14, {'os': 'windows', 'screen': {'max_width': 1600, 'max_height': 900}}),
    ('screen-min', 15, {'os': 'linux', 'screen': {'min_width': 2560, 'min_height': 1440}}),
    ('screen-exact', 16, {'os': 'windows', 'screen': {
        'min_width': 1920, 'max_width': 1920, 'min_height': 1080, 'max_height': 1080}}),
    ('window', 17, {'os': 'windows', 'window': [1280, 720]}),
    ('window-and-screen', 18, {'os': 'linux', 'window': [1000, 700], 'screen': {'max_width': 1920}}),
    ('display-bound', 19, {'os': 'windows', 'headless': False, 'hasDisplay': True}),
    ('virtual-display', 20, {
        'os': 'linux', 'headless': False, 'virtual_display': ':99', 'virtual_display_size': [1366, 768],
        'env': {**ENV, 'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'},
    }),
    ('fixed-config', 21, {
        'os': 'windows', 'i_know_what_im_doing': True,
        'config': {
            'navigator.hardwareConcurrency': 6, 'timezone': 'America/Chicago',
            'navigator.languages': ['es-MX', 'es'], 'screen.width': 1440, 'screen.height': 900,
            'geolocation:latitude': 41.8781, 'geolocation:longitude': -87.6298,
        },
    }),
    ('fixed-config-warns', 22, {
        'os': 'linux',
        'config': {'navigator.deviceMemory': 8, 'window.outerWidth': 1200, 'window.outerHeight': 800},
    }),
    ('pinned-user-agent', 23, {'config': {'navigator.userAgent': PINNED_UA}}),
    ('pinned-user-agent-unicode', 24, {
        'host': 'win',
        'config': {'navigator.userAgent': PINNED_UA, 'fonts': ['Arial', 'Ünïcödé Sans', '明朝', 'Emoji \U0001F600 Font'] * 180},
        'i_know_what_im_doing': True,
    }),
    ('humanize-true', 25, {'os': 'windows', 'humanize': True}),
    ('humanize-seed', 26, {'os': 'linux', 'humanize': {'seed': 7}}),
    ('humanize-seconds', 27, {'os': 'linux', 'humanize': 1.5}),
    ('canvas-noise', 28, {'os': 'windows', 'canvas_noise': True}),
    ('pin-cpu-cores', 29, {'os': 'windows', 'pin_cpu_cores': True}),
    ('fonts', 30, {'os': 'linux', 'fonts': ['Comic Neue', 'Arial', 'Comic Neue']}),
    ('custom-fonts-only', 31, {'os': 'windows', 'fonts': ['Inter', 'Inter', 'Roboto'], 'custom_fonts_only': True}),
    ('blocks', 32, {
        'os': 'linux', 'block_images': True, 'block_webrtc': True, 'block_webgl': True, 'enable_cache': True,
    }),
    ('args', 33, {
        'os': 'windows', 'args': ['--foo=bar', '--disable-features=Translate', '--enable-features=A,B'],
        'ignore_default_args': ['--mute-audio', '--enable-automation'], 'slow_mo': 5,
    }),
    ('args-dropped', 34, {
        'os': 'linux', 'args': ['--lang=fr', '--no-sandbox', '--window-size=1,1'], 'i_know_what_im_doing': True,
        'debug': True,
    }),
    ('ignore-all-default-args', 35, {'os': 'linux', 'ignore_default_args': True}),
    ('chrome-version', 36, {'os': 'windows', 'chrome_version': 140}),
    ('own-chrome-version', 37, {'os': 'linux', 'chrome_version': 152}),
    ('no-default-addons', 38, {'os': 'linux', 'exclude_addons': ['UBO']}),
    ('headless-none', 39, {'os': 'windows', 'headless': None}),
    ('main-world-eval', 40, {'os': 'linux', 'main_world_eval': True, 'timeout': 12000}),
    ('addons', 44, {'os': 'windows', 'addons': ['<FIXTURES>/addon'], 'args': ['--disable-features=Translate']}),
    ('humanize-raw', 45, {'os': 'linux', 'humanize': False}),
    ('macos-override', 41, {'os': 'macos', 'allow_os_mismatch': True}),
    ('allow-mismatch-any-os', 42, {'allow_os_mismatch': True}),
    ('webgl-config', 43, {'os': 'windows', 'webgl_config': 'FIRST'}),
    # errors
    ('error-macos', 50, {'os': 'macos'}),
    ('error-os-case', 51, {'os': 'Windows'}),
    ('error-os-unknown', 52, {'os': 'android'}),
    ('error-linux-from-windows', 53, {'host': 'win', 'os': 'linux'}),
    ('error-firefox-prefs', 54, {'firefox_user_prefs': {'a': 1}}),
    ('error-ff-version', 55, {'ff_version': 135}),
    ('error-cpu', 56, {'cpu': {'arch': 'arm'}}),
    ('error-preset', 57, {'fingerprint_preset': True}),
    ('error-disable-coop', 58, {'disable_coop': True}),
    ('error-addon-new-tab', 59, {'allow_addon_new_tab': True}),
    ('error-unknown-property', 60, {'os': 'linux', 'config': {'no.such.key': 1}}),
    ('error-property-type', 61, {'os': 'linux', 'config': {'timezone': 5}}),
    ('error-webgl-without-os', 62, {'webgl_config': ['Google Inc. (Intel)', 'x']}),
    ('error-webgl-unknown', 63, {'os': 'linux', 'webgl_config': ['Nobody', "It's not a GPU"]}),
    ('error-webgl-with-user-agent', 64, {
        'os': 'windows', 'webgl_config': 'FIRST', 'config': {'navigator.userAgent': PINNED_UA}}),
    ('error-custom-fonts-only', 65, {'os': 'linux', 'custom_fonts_only': True}),
    ('error-conflicting-switch', 66, {'os': 'linux', 'args': ['--user-agent=Mozilla/5.0']}),
    ('error-chromium-prefs', 67, {'os': 'linux', 'chromium_prefs': {'credentials_enable_service': False}}),
    ('error-incoherent', 68, {
        'os': 'windows', 'config': {'navigator.platform': 'MacIntel'}}),
    ('error-browser-and-path', 69, {'browser': 'official/alpha.1'}),
    ('error-user-agent-os', 70, {'config': {'navigator.userAgent': 'Mozilla/5.0 (PlayStation 5)'}}),
    ('error-humanize', 71, {'os': 'linux', 'humanize': 'fast'}),
    ('error-addon-path', 72, {'os': 'linux', 'addons': ['/no/such/extension']}),
]


class Display:
    def __init__(self, width, height):
        self.width, self.height = width, height


def make_bundle() -> Path:
    BUNDLE.mkdir(parents=True, exist_ok=True)
    version = BUNDLE / 'version.json'
    wanted = json.dumps({'version': '152.0.7977.42', 'release': 'alpha.1', 'milestone': 152})
    if not version.exists() or version.read_text() != wanted:
        version.write_text(wanted)
    binary = BUNDLE / 'camouwolf'
    if not binary.exists():
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
    return binary


def run(case_options, binary, scratch):
    options = dict(case_options)
    host = options.pop('host', 'lin')
    has_display = options.pop('hasDisplay', False)

    utils.OS_NAME = host
    utils.INSTALL_DIR = scratch / 'cache'
    addons.ADDONS_DIR = scratch / 'cache' / 'addons'
    utils.warn_if_package_outdated = lambda: None
    utils.ensure_browser_profile_dir = lambda env=None: None
    fingerprints.host_cpu_count = lambda: HOST['cpuCount']
    cpu_affinity.supported = lambda: HOST['canPin']
    utils.has_display = lambda env=None: has_display
    utils.largest_display = lambda: Display(*HOST['display'])
    utils.public_ip = lambda proxy=None: HOST['publicIp']
    utils.public_ipv6 = lambda: None
    utils.geoip_allowed = lambda: None
    geo = HOST['geolocation']
    utils.get_geolocation = lambda ip, geoip_db=None: Geolocation(
        locale=Locale(language=geo['language'], region=geo['region'], script=geo['script']),
        longitude=geo['longitude'], latitude=geo['latitude'], timezone=geo['timezone'], accuracy=geo['accuracy'],
    )
    utils.secrets.randbelow = lambda n: HOST['canvasSeed'] - 1

    options.setdefault('env', dict(ENV))
    options.setdefault('headless', True)
    options['executable_path'] = binary
    if 'addons' in options:
        options['addons'] = [path.replace('<FIXTURES>', str(FIXTURES)) for path in options['addons']]
    if 'screen' in options:
        options['screen'] = Screen(**options['screen'])
    if 'window' in options:
        options['window'] = tuple(options['window'])
    if 'virtual_display_size' in options:
        options['virtual_display_size'] = tuple(options['virtual_display_size'])
    if 'exclude_addons' in options:
        options['exclude_addons'] = [DefaultAddons[name] for name in options['exclude_addons']]
    if options.get('webgl_config') == 'FIRST':
        # The first recorded pair in sorted order; the resolved pair is what
        # the golden file carries.
        options['webgl_config'] = sorted(utils.chrome_gpus(options['os']))[0]
        case_options['webgl_config'] = list(options['webgl_config'])
    elif 'webgl_config' in options:
        options['webgl_config'] = tuple(options['webgl_config'])
    return options


def portable(value, scratch):
    """`value` with this machine's paths replaced by placeholders."""
    if isinstance(value, str):
        return (
            value.replace(str(BUNDLE), '<BUNDLE>')
            .replace(str(FIXTURES), '<FIXTURES>')
            .replace(str(scratch / 'cache'), '<CACHE>')
        )
    if isinstance(value, (list, tuple)):
        return [portable(item, scratch) for item in value]
    return value


def record(name, seed, case_options, binary, scratch):
    import contextlib
    import io

    options = run(case_options, binary, scratch)
    random.seed(seed)
    out = io.StringIO()
    entry = {'name': name, 'seed': seed, 'options': case_options}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        try:
            with contextlib.redirect_stdout(out):
                result = utils.launch_options(**options)
        except Exception as error:  # noqa: BLE001
            message = error.args[0] if isinstance(error, KeyError) and error.args else str(error)
            entry['error'] = {'type': type(error).__name__, 'message': portable(str(message), scratch)}
        else:
            result['executable_path'] = portable(str(result['executable_path']), scratch)
            result['args'] = portable(result['args'], scratch)
            # The config is compared as the bytes the browser reads; a JSON
            # round trip would lose Python's int/float distinction.
            entry['result'] = result
    entry['warnings'] = [[w.category.__name__, portable(str(w.message), scratch)] for w in caught]
    # pprint(config) is the one line whose layout differs; the rest is compared.
    entry['stdout'] = [line for line in out.getvalue().splitlines() if line.startswith('[camouwolf]')]
    return entry


def main() -> int:
    binary = make_bundle()
    with tempfile.TemporaryDirectory(prefix='camouwolf-golden-') as tmp:
        cases = [record(name, seed, options, binary, Path(tmp)) for name, seed, options in CASES]
    text = json.dumps({'host': HOST, 'env': ENV, 'cases': cases}, indent=1, ensure_ascii=False) + '\n'
    if '--check' in sys.argv:
        if not GOLDEN.exists() or GOLDEN.read_text(encoding='utf-8') != text:
            print(f'{GOLDEN} is not what the Python launcher in this checkout produces; run pnpm golden')
            return 1
        return 0
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(text, encoding='utf-8')
    errors = sum('error' in case for case in cases)
    print(f'{GOLDEN}: {len(cases)} cases ({errors} errors)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
