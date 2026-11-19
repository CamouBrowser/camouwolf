"""An fpgen Chrome fingerprint becomes the config the browser reads."""

import pytest

from camouwolf import fingerprints, webgl
from camouwolf.fingerprints import from_fpgen

WINDOWS_FINGERPRINT = {
    'navigator': {
        'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'platform': 'Win32', 'hardwareConcurrency': 12, 'deviceMemory': 8, 'oscpu': 'undefined',
    },
    'screen': {'width': 1920, 'height': 1080, 'availWidth': 1920, 'availHeight': 1040,
               'availLeft': 0, 'availTop': 0, 'colorDepth': 24},
    'window': {'outerWidth': 1936, 'outerHeight': 1056, 'screenX': -8, 'screenY': -8, 'devicePixelRatio': 1},
    'devices': {'audioinput': 1, 'videoinput': 1, 'audiooutput': 2},
    'gpu': {'vendor': 'Google Inc. (NVIDIA)',
            'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)'},
    'webgl': {'params': {
        '3379': {'name': 'MAX_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
        '34076': {'name': 'MAX_CUBE_MAP_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
        '34047': {'name': 'MAX_TEXTURE_MAX_ANISOTROPY_EXT', 'type': 'Number', 'value': 16},
        '3386': {'name': 'MAX_VIEWPORT_DIMS', 'type': 'Int32Array', 'value': [32767, 32767]},
        '7938': {'name': 'VERSION', 'type': 'String', 'value': 'WebGL 1.0 (OpenGL ES 2.0 Chromium)'},
        '2884': {'name': 'CULL_FACE', 'type': 'Boolean', 'value': False},
        '34930': {'name': 'MAX_TEXTURE_IMAGE_UNITS', 'type': 'Number', 'value': 16},
    }},
    'webgl2': {'params': {
        '3379': {'name': 'MAX_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
        '34076': {'name': 'MAX_CUBE_MAP_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
        '34930': {'name': 'MAX_TEXTURE_IMAGE_UNITS', 'type': 'Number', 'value': 32},
    }},
    'gpuInfo': {'adapter': {'default': {'info': {'vendor': 'nvidia', 'architecture': 'ampere', 'device': ''}}}},
    'allFonts': ['Arial', 'Calibri', 'Segoe UI'],
    'voices': [{'default': True, 'lang': 'en-US', 'localService': True,
                'name': 'Microsoft David - English (United States)',
                'voiceURI': 'Microsoft David - English (United States)'}],
}


def test_the_mapped_keys_and_their_values():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)

    assert config['navigator.platform'] == 'Win32'
    assert config['navigator.hardwareConcurrency'] == 12 and config['navigator.deviceMemory'] == 8
    assert config['screen.width'] == 1920 and config['screen.availHeight'] == 1040
    assert config['screen.colorDepth'] == 24
    assert config['window.outerWidth'] == 1936 and config['window.outerHeight'] == 1056
    assert config['webGl:vendor'] == 'Google Inc. (NVIDIA)'
    assert 'RTX 3060' in config['webGl:renderer']
    assert (config['webGpu:vendor'], config['webGpu:architecture'], config['webGpu:device']) == ('nvidia', 'ampere', '')


def test_the_user_agent_is_rewritten_to_the_running_milestone():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)
    assert 'Chrome/152.0.0.0' in config['navigator.userAgent'] and 'Chrome/143' not in config['navigator.userAgent']
    assert 'Chrome/143.0.0.0' in from_fpgen(WINDOWS_FINGERPRINT)['navigator.userAgent']


def test_only_limits_both_context_versions_agree_on_are_sent():
    params = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)['webGl:parameters']
    # Dropped: an extension limit, an array, a string, state, and a limit
    # WebGL2 reports differently.
    assert params == {'3379': 16384, '34076': 16384}


def test_fonts_voices_and_devices_take_the_browsers_formats():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)

    assert {'Arial', 'Calibri', 'Segoe UI'} <= set(config['fonts'])
    assert config['voices'] == [
        'Microsoft David - English (United States)|en-US|Microsoft David - English (United States)|1|1'
    ]
    kinds = [entry.split('|')[0] for entry in config['mediaDevices:enumerated']]
    # Without media permission Chrome lists one unlabelled device per kind,
    # however many the machine has.
    assert kinds == ['audioinput', 'videoinput', 'audiooutput']
    assert all(entry.endswith('|||') for entry in config['mediaDevices:enumerated'])


def test_every_mapped_key_is_in_the_schema():
    from camouwolf import utils

    utils.validate_config(from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152))


def test_keys_the_browser_cannot_read_are_not_sent():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)
    assert 'navigator.oscpu' not in config


def test_device_pixel_ratio_is_sent():
    # The browser renders at it (0025), so the claimed ratio is the real one.
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)
    assert config['window.devicePixelRatio'] == WINDOWS_FINGERPRINT['window']['devicePixelRatio']


@pytest.mark.parametrize('renderer, target, ok', [
    ('ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)', 'win', True),
    ('ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)', 'lin', False),
    ('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 'mac', True),
    ('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 'win', False),
    ('ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)', 'lin', True),
    ('ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)', 'windows', False),
])
def test_a_renderer_names_its_angle_backend(renderer, target, ok):
    assert webgl.backend_matches_os(renderer, target) is ok


def test_software_renderers_are_not_claimable():
    swiftshader = {'vendor': 'Google Inc. (Google)',
                   'renderer': 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)'}
    assert not webgl.usable_gpu(swiftshader)
    assert not webgl.usable_gpu(None) and not webgl.usable_gpu({'vendor': '', 'renderer': ''})
    assert webgl.usable_gpu(WINDOWS_FINGERPRINT['gpu'], 'win')
    assert not webgl.usable_gpu(WINDOWS_FINGERPRINT['gpu'], 'lin')


@pytest.mark.parametrize('os_name, key', [('windows', 'win'), ('linux', 'lin'), ('macos', 'mac')])
def test_a_drawn_fingerprint_is_chrome_on_the_asked_os(os_name, key):
    fingerprint = fingerprints.generate_fingerprint(os=os_name)

    assert 'Chrome/' in fingerprint['navigator']['userAgent']
    assert fingerprints.fingerprint_os(fingerprint) == key
    from camouwolf import utils
    utils.validate_config(from_fpgen(fingerprint, chrome_version=152))


def test_recorded_gpus_are_hardware_on_the_os_backend():
    gpus = webgl.chrome_gpus('windows')
    assert gpus and all(webgl.backend_matches_os(renderer, 'win') for _vendor, renderer in gpus)
