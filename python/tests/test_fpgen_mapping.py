"""An fpgen Chrome fingerprint becomes the config the browser reads."""

import pytest

from camouwolf import fingerprints, webgl
from camouwolf.fingerprints import from_fpgen

RENDERER = 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)'

WINDOWS_FINGERPRINT = {
    'navigator': {
        'userAgent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        'platform': 'Win32', 'hardwareConcurrency': 12, 'deviceMemory': 8, 'oscpu': 'undefined',
    },
    'screen': {'width': 1920, 'height': 1080, 'availWidth': 1920, 'availHeight': 1040,
               'availLeft': 0, 'availTop': 0, 'colorDepth': 24},
    'window': {'outerWidth': 1936, 'outerHeight': 1056, 'screenX': -8, 'screenY': -8, 'devicePixelRatio': 1},
    'devices': {'audioinput': 1, 'videoinput': 1, 'audiooutput': 2},
    'gpu': {'vendor': 'Google Inc. (NVIDIA)', 'renderer': RENDERER},
    'webgl': {
        'renderer': RENDERER,
        'params': {
            '3379': {'name': 'MAX_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
            '34047': {'name': 'MAX_TEXTURE_MAX_ANISOTROPY_EXT', 'type': 'Number', 'value': 16},
            '3386': {'name': 'MAX_VIEWPORT_DIMS', 'type': 'Int32Array', 'value': [32767, 32767]},
            '33902': {'name': 'ALIASED_LINE_WIDTH_RANGE', 'type': 'Float32Array', 'value': [1, 1]},
            '3408': {'name': 'SUBPIXEL_BITS', 'type': 'Number', 'value': 4},
            '7938': {'name': 'VERSION', 'type': 'String', 'value': 'WebGL 1.0 (OpenGL ES 2.0 Chromium)'},
            '2884': {'name': 'CULL_FACE', 'type': 'Boolean', 'value': False},
            '2978': {'name': 'VIEWPORT', 'type': 'Int32Array', 'value': [0, 0, 300, 150]},
            '34930': {'name': 'MAX_TEXTURE_IMAGE_UNITS', 'type': 'Number', 'value': 16},
        },
        'supportedExtensions': ['ANGLE_instanced_arrays', 'WEBGL_draw_buffers'],
        'shaderPrecisionFormats': [
            {'shaderType': 35633, 'precisionType': 36337,
             'shaderPrecisionFormat': {'rangeMin': 127, 'rangeMax': 127, 'precision': 23}},
        ],
    },
    'webgl2': {
        'renderer': RENDERER,
        'params': {
            '3379': {'name': 'MAX_TEXTURE_SIZE', 'type': 'Number', 'value': 16384},
            '34930': {'name': 'MAX_TEXTURE_IMAGE_UNITS', 'type': 'Number', 'value': 32},
            '35076': {'name': 'MIN_PROGRAM_TEXEL_OFFSET', 'type': 'Number', 'value': -8},
            '37447': {'name': 'MAX_CLIENT_WAIT_TIMEOUT_WEBGL', 'type': 'Number', 'value': 0},
        },
        'supportedExtensions': ['OVR_multiview2'],
        'shaderPrecisionFormats': [],
    },
    'gpuInfo': {
        'unmaskedRenderer': RENDERER,
        'preferredCanvasFormat': 'bgra8unorm',
        'adapter': {'default': {
            'info': {'vendor': 'nvidia', 'architecture': 'ampere', 'device': '',
                     'subgroupMinSize': 32, 'subgroupMaxSize': 128},
            'features': ['shader-f16', 'texture-compression-bc'],
            'limits': {'maxBufferSize': 2147483648, 'maxComputeWorkgroupStorageSize': 32768},
        }},
    },
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


def test_each_context_version_claims_its_own_device_limits():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)
    # Dropped: an extension limit, a string, state (CULL_FACE, VIEWPORT) and a
    # WebGL2 limit named like an extension's.
    assert config['webGl:parameters'] == {
        '3379': 16384, '3386': [32767, 32767], '33902': [1, 1], '3408': 4, '34930': 16,
    }
    assert config['webGl2:parameters'] == {'3379': 16384, '34930': 32, '35076': -8}
    assert config['webGl:supportedExtensions'] == ['ANGLE_instanced_arrays', 'WEBGL_draw_buffers']
    assert config['webGl2:supportedExtensions'] == ['OVR_multiview2']
    assert config['webGl:shaderPrecisionFormats'] == {
        '35633,36337': {'rangeMin': 127, 'rangeMax': 127, 'precision': 23},
    }
    assert 'webGl2:shaderPrecisionFormats' not in config


def test_a_webgl_readout_of_another_gpu_is_not_claimed():
    # fpgen occasionally pairs one device's GPU with another's WebGL readout;
    # its limits would contradict the claimed renderer.
    other = {**WINDOWS_FINGERPRINT, 'webgl2': {**WINDOWS_FINGERPRINT['webgl2'], 'renderer': 'ANGLE (Intel, other)'}}
    config = from_fpgen(other, chrome_version=152)
    assert not [key for key in config if key.startswith(('webGl:p', 'webGl:s', 'webGl2:'))]


def test_the_webgpu_adapter_is_claimed_whole():
    config = from_fpgen(WINDOWS_FINGERPRINT, chrome_version=152)
    assert config['webGpu:preferredCanvasFormat'] == 'bgra8unorm'
    assert (config['webGpu:subgroupMinSize'], config['webGpu:subgroupMaxSize']) == (32, 128)
    assert config['webGpu:features'] == ['shader-f16', 'texture-compression-bc']
    assert config['webGpu:limits'] == {'maxBufferSize': 2147483648, 'maxComputeWorkgroupStorageSize': 32768}


def test_an_adapter_of_another_gpu_is_not_claimed_but_the_os_canvas_format_is():
    other = {**WINDOWS_FINGERPRINT, 'gpuInfo': {**WINDOWS_FINGERPRINT['gpuInfo'], 'unmaskedRenderer': 'ANGLE (Intel, other)'}}
    config = from_fpgen(other, chrome_version=152)
    assert config['webGpu:preferredCanvasFormat'] == 'bgra8unorm'
    assert not [key for key in config if key.startswith('webGpu:') and key != 'webGpu:preferredCanvasFormat']


def test_a_linux_identity_keeps_the_browsers_canvas_format():
    assert 'webGpu:preferredCanvasFormat' not in fingerprints.webgpu_adapter(
        WINDOWS_FINGERPRINT['gpuInfo'], RENDERER, 'lin'
    )


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


@pytest.mark.parametrize('target_os', ['win', 'mac', 'lin'])
def test_fonts_the_bundle_cannot_render_are_not_claimed(target_os):
    # fpgen records name families no bundled OS ships (CI caught 109 of 150 on
    # one Linux identity); each one measures as missing in the page.
    claimed = fingerprints.font_list(['Abyssinica SIL', 'Arial', 'arial'], target_os)

    assert 'Abyssinica SIL' not in claimed
    assert claimed.count('Arial') + claimed.count('arial') == 1
    assert {f.lower() for f in claimed} <= fingerprints.BUNDLE_FONTS[target_os]


@pytest.mark.parametrize('target_os', ['win', 'mac', 'lin'])
def test_every_essential_font_is_in_the_bundle(target_os):
    missing = [f for f in fingerprints.ESSENTIAL_FONTS[target_os]
               if f.lower() not in fingerprints.BUNDLE_FONTS[target_os]]
    assert not missing


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
