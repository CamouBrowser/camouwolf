"""The identity is checked against itself on every launch."""

from camouwolf import clienthints, coherence


def windows():
    config = clienthints.client_hint_config(clienthints.ChromeIdentity(152, '152.0.7977.42', 'win'))
    config.update({
        'navigator.hardwareConcurrency': 8, 'navigator.deviceMemory': 8,
        'screen.width': 1920, 'screen.height': 1080, 'screen.availWidth': 1920, 'screen.availHeight': 1040,
        'screen.availLeft': 0, 'screen.availTop': 0, 'screen.colorDepth': 24,
        'window.outerWidth': 1600, 'window.outerHeight': 900, 'window.screenX': 0, 'window.screenY': 0,
        'webGl:vendor': 'Google Inc. (NVIDIA)',
        'webGl:renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)',
    })
    return config


def rules(violations):
    return {violation.rule for violation in violations}


def test_a_coherent_identity_has_no_violations():
    assert coherence.validate(windows(), 'win') == []
    assert coherence.apply(windows(), 'win') == []


def test_client_hints_that_contradict_the_user_agent_are_fatal():
    config = {**windows(), 'userAgentData.platform': 'macOS'}
    assert 'client-hints' in rules(coherence.validate(config, 'win'))
    assert 'client-hints' in coherence.FATAL_RULES


def test_a_gpu_on_a_backend_the_os_never_uses_is_fatal():
    config = {**windows(), 'webGl:renderer': 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)'}
    violations = coherence.apply(config, 'win')
    assert 'gpu-matches-os' in rules(violations) and 'gpu-matches-os' in coherence.FATAL_RULES
    # Not repaired: there is no right GPU to invent.
    assert 'Metal' in config['webGl:renderer']


def test_repairable_values_are_repaired_when_the_launch_drew_them():
    config = {**windows(), 'screen.colorDepth': 32, 'navigator.deviceMemory': 6,
              'screen.availHeight': 1200, 'window.outerWidth': 2400}
    violations = coherence.apply(config, 'win')

    assert not [v for v in violations if v.kept]
    assert config['screen.colorDepth'] == 24
    assert config['navigator.deviceMemory'] in (0.25, 0.5, 1, 2, 4, 8)
    assert config['screen.availHeight'] <= 1080 and config['window.outerWidth'] <= 1920
    assert coherence.validate(config, 'win') == []


def test_a_callers_value_is_kept_and_reported():
    config = {**windows(), 'screen.colorDepth': 32}
    violations = coherence.apply(config, 'win', keep=frozenset({'screen.colorDepth'}))

    assert config['screen.colorDepth'] == 32
    assert [v.kept for v in violations if v.rule == 'color-depth'] == [('screen.colorDepth',)]


def test_a_portrait_desktop_screen_is_turned():
    config = {**windows(), 'screen.width': 1080, 'screen.height': 1920,
              'screen.availWidth': 1080, 'screen.availHeight': 1880}
    assert coherence.screen_is_implausible(config)
    assert coherence.repair_screen_orientation(config)
    assert (config['screen.width'], config['screen.height']) == (1920, 1080)


def test_a_zoomed_ratio_becomes_the_display_behind_it():
    # A scraped 1760x990 at 1.0909 is a 1920x1080 monitor at 100% with the page
    # zoomed to 110%: no Windows display setting offers 1.0909.
    config = {**windows(), 'window.devicePixelRatio': 1.0909, 'screen.width': 1760, 'screen.height': 990,
              'screen.availWidth': 1760, 'screen.availHeight': 953, 'window.outerWidth': 1466, 'window.outerHeight': 825}
    assert 'device-pixel-ratio' in rules(coherence.validate(config, 'win'))

    coherence.repair_device_pixel_ratio(config, 'win')
    assert config['window.devicePixelRatio'] == 1
    assert (config['screen.width'], config['screen.height']) == (1920, 1080)
    assert config['window.outerWidth'] == 1599
    assert coherence.validate(config, 'win') == []


def test_a_display_step_is_left_alone():
    config = {**windows(), 'window.devicePixelRatio': 1.25}
    coherence.repair_device_pixel_ratio(config, 'win')
    assert config == {**windows(), 'window.devicePixelRatio': 1.25}
    # macOS offers 1 and 2 only.
    assert coherence.is_zoomed_ratio(1.25, 'mac') and not coherence.is_zoomed_ratio(2, 'mac')


def test_a_callers_ratio_is_kept_and_reported():
    config = {**windows(), 'window.devicePixelRatio': 1.0909}
    violations = coherence.apply(config, 'win', keep=frozenset({'window.devicePixelRatio'}))

    assert config['window.devicePixelRatio'] == 1.0909 and config['screen.width'] == 1920
    assert [v.kept for v in violations if v.rule == 'device-pixel-ratio'] == [('window.devicePixelRatio',)]


APPLE_M2 = 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)'
INTEL_MAC = 'ANGLE (Intel, ANGLE Metal Renderer: Intel(R) Iris(TM) Plus Graphics 655, Unspecified Version)'


def test_a_mac_is_a_machine_apple_made():
    # fpgen's records include browsers that randomise hardwareConcurrency, and
    # its screens and GPUs are drawn apart.
    assert coherence.machine_misfit(APPLE_M2, 'mac', 8, 1512, 982) is None
    assert coherence.machine_misfit(APPLE_M2, 'mac', 6, 1512, 982)
    assert coherence.machine_misfit(APPLE_M2, 'mac', 9, 1512, 982)
    assert coherence.machine_misfit(INTEL_MAC, 'mac', 8, 1440, 900) is None
    assert coherence.machine_misfit(INTEL_MAC, 'mac', 7, 1440, 900)
    assert coherence.machine_misfit(INTEL_MAC, 'mac', 8, 1512, 982)
    assert coherence.machine_misfit(INTEL_MAC, 'win', 7, 1512, 982) is None


def test_depth_and_touch_agree_with_the_machine():
    config = {**windows(), 'screen.colorDepth': 30, 'screen.pixelDepth': 24, 'navigator.maxTouchPoints': 10}
    assert coherence.apply(config, 'mac') and config['screen.pixelDepth'] == 30
    assert config['navigator.maxTouchPoints'] == 0
    assert {'pixel-depth', 'touch-points'}.isdisjoint(rules(coherence.validate(config, 'mac')))
    # A Windows laptop can have a touchscreen.
    assert 'touch-points' not in rules(coherence.validate({**windows(), 'navigator.maxTouchPoints': 10}, 'win'))


def test_the_architecture_is_the_one_the_gpu_comes_with():
    adreno = 'ANGLE (Qualcomm, Qualcomm(R) Adreno(TM) 8cx Gen 3 Direct3D11 vs_5_0 ps_5_0, D3D11)'
    config = {**windows(), 'webGl:renderer': adreno}
    assert 'architecture' in rules(coherence.validate(config, 'win'))
    coherence.apply(config, 'win')
    assert config['userAgentData.architecture'] == 'arm'


def test_a_gpu_names_the_cpu_it_can_sit_beside():
    assert coherence.gpu_cpu('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 'mac') == (
        'arm', ('apple',))
    assert coherence.gpu_cpu('ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)', 'win') == (
        'x86', ('intel',))
    assert coherence.gpu_cpu('ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)', 'win') == (
        'x86', ('amd',))
    assert coherence.gpu_cpu(windows()['webGl:renderer'], 'win') == ('x86', ())
    assert coherence.gpu_cpu('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)', 'lin') == (
        None, ())


def test_the_cpu_identity_is_the_one_the_identity_determines():
    intel_igpu = 'ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)'
    discrete = windows()['webGl:renderer']
    assert coherence.cpu_identity('ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)', 8, 'mac',
                                  'arm', None) == 'arm64'
    assert coherence.cpu_identity(intel_igpu, 8, 'win', 'x86', None) == 'intel'
    assert coherence.cpu_identity(discrete, 14, 'win', 'x86', None) == 'intel'
    assert coherence.cpu_identity(discrete, 16, 'win', 'x86', 'amd') == 'amd'
    # Any x86 maker fits: the host's arithmetic is one of them.
    assert coherence.cpu_identity(discrete, 16, 'win', 'x86', None) is None
    assert coherence.cpu_identity('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)', 8, 'lin',
                                  None, None) is None


def test_a_cpu_the_machine_cannot_have_is_named():
    intel_igpu = 'ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)'
    assert coherence.cpu_misfit(intel_igpu, 8, 'win', 'x86', 'amd')
    assert coherence.cpu_misfit(windows()['webGl:renderer'], 14, 'win', 'x86', 'amd')
    assert coherence.cpu_misfit(windows()['webGl:renderer'], 16, 'win', 'x86', 'amd') is None
