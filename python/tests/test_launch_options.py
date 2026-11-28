"""launch_options(): the options, and what each one turns into."""

import json
import random
import re
import warnings

import pytest

from camouwolf import DefaultAddons, addons, coherence, switches, utils, webgl
from camouwolf._warnings import LeakWarning
from camouwolf.exceptions import (
    IncoherentIdentity,
    InvalidAddonPath,
    InvalidOS,
    NonChromeFingerprint,
    UnknownIPLocation,
    UnknownProperty,
    UnsupportedOSSpoof,
)
from camouwolf.fingerprints import Screen, chrome_viewport_width, chrome_window_reports
from camouwolf.webgl import chrome_gpus


@pytest.fixture(autouse=True)
def linux_host(monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')


def config_of(launch):
    return utils.launch_config(launch)


# ---- the result ------------------------------------------------------------


def test_the_result_is_playwright_chromium_launch_options(options, bundle):
    launch = options(os='linux', slow_mo=5)

    assert launch['executable_path'] == str(bundle)
    assert launch['headless'] is True and launch['slow_mo'] == 5
    assert set(switches.IGNORED_DEFAULT_ARGS) <= set(launch['ignore_default_args'])
    assert all(switch in launch['args'] for switch in switches.BASE_SWITCHES)
    assert not [key for key in launch if key.startswith('_')]
    assert 'firefox_user_prefs' not in launch


def test_the_callers_mappings_are_not_written_to(options):
    config, args, env = {'timezone': 'Europe/Berlin'}, ['--foo'], {'HOME': '/tmp'}
    options(os='linux', config=config, args=args, env=env, i_know_what_im_doing=True)
    assert (config, args, env) == ({'timezone': 'Europe/Berlin'}, ['--foo'], {'HOME': '/tmp'})


def test_the_environment_variable_names_the_binary(bundle, monkeypatch):
    monkeypatch.setenv('CAMOUWOLF_EXECUTABLE_PATH', str(bundle))
    launch = utils.launch_options(os='linux', headless=True, exclude_addons=[DefaultAddons.UBO])
    assert launch['executable_path'] == str(bundle)


# ---- the identity ----------------------------------------------------------


@pytest.mark.parametrize('os_name, platform, ch_platform', [
    ('windows', 'Win32', 'Windows'), ('linux', 'Linux x86_64', 'Linux'),
])
def test_the_identity_is_chrome_on_the_asked_os_at_the_binarys_version(options, os_name, platform, ch_platform):
    config = config_of(options(os=os_name))

    assert 'Chrome/152.0.0.0' in config['navigator.userAgent']
    assert config['navigator.platform'] == platform
    assert config['userAgentData.platform'] == ch_platform
    assert config['userAgentData.uaFullVersion'] == '152.0.7977.42'
    assert coherence.validate(config, utils.get_target_os(config)) == []
    assert webgl.usable_gpu({'vendor': config['webGl:vendor'], 'renderer': config['webGl:renderer']}, os_name)


def test_the_default_os_is_one_this_host_can_pass_for(options):
    seen = {utils.launch_os(options()) for _ in range(12)}
    assert seen <= {'linux', 'windows'}


def test_macos_from_linux_is_refused_unless_overridden(options):
    with pytest.raises(UnsupportedOSSpoof, match='macos'):
        options(os='macos')
    assert utils.launch_os(options(os='macos', allow_os_mismatch=True)) == 'macos'


def test_a_windows_host_passes_for_windows_only(options, monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'win')
    with pytest.raises(UnsupportedOSSpoof):
        options(os='linux')


def test_an_invalid_os_is_refused(options):
    with pytest.raises(InvalidOS):
        options(os='Windows')
    with pytest.raises(InvalidOS):
        options(os='android')


def test_another_milestone_warns_and_is_claimed_consistently(options):
    with pytest.warns(LeakWarning, match='milestone'):
        config = config_of(options(os='windows', chrome_version=150))
    assert 'Chrome/150.0.0.0' in config['navigator.userAgent']
    assert config['userAgentData.uaFullVersion'].startswith('150.')


def test_a_passed_fingerprint_is_used(options):
    # The launcher's own draw: a raw fpgen record may have a software GPU,
    # which a passed fingerprint is refused for.
    fingerprint = utils.draw_fingerprint('windows', None, None, None)
    with pytest.warns(LeakWarning):
        config = config_of(options(fingerprint=fingerprint))
    assert config['webGl:renderer'] == fingerprint['gpu']['renderer']


def test_a_fingerprint_from_another_browser_is_refused(options):
    fingerprint = {'navigator': {'userAgent': 'Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0'}}
    with pytest.raises(NonChromeFingerprint):
        options(fingerprint=fingerprint)


def test_webgl_config_draws_a_machine_with_that_gpu(options):
    from camouwolf.webgl import chrome_gpus

    vendor, renderer = sorted(chrome_gpus('windows'))[0]
    config = config_of(options(os='windows', webgl_config=(vendor, renderer)))
    assert (config['webGl:vendor'], config['webGl:renderer']) == (vendor, renderer)

    with pytest.raises(ValueError, match='No recorded Chrome device'):
        options(os='windows', webgl_config=('Google Inc. (NVIDIA)', 'ANGLE (made up)'))
    with pytest.raises(ValueError, match='OS must be set'):
        options(webgl_config=(vendor, renderer))


def test_caller_config_wins_and_is_kept(options):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        config = config_of(options(os='windows', config={'navigator.hardwareConcurrency': 6, 'timezone': 'Asia/Tokyo'}))
    assert config['navigator.hardwareConcurrency'] == 6 and config['timezone'] == 'Asia/Tokyo'


def test_the_battery_is_a_desktop_on_mains_power_unless_claimed(options):
    config = config_of(options(os='windows'))
    assert (config['battery:charging'], config['battery:level']) == (True, 1.0)

    config = config_of(options(os='windows', config={'battery:charging': False, 'battery:level': 0.42}))
    assert (config['battery:charging'], config['battery:level']) == (False, 0.42)


def test_the_media_codecs_are_the_drawn_machines(options):
    # Windows encodes AAC with Media Foundation; Chrome on Linux has no AAC encoder.
    assert 'aac' in config_of(options(os='windows'))['media:platformEncoders']
    assert 'aac' not in config_of(options(os='linux'))['media:platformEncoders']


def test_a_self_contradicting_identity_is_refused(options):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with pytest.raises(IncoherentIdentity, match='Sec-CH-UA-Platform'):
            options(os='windows', config={'userAgentData.platform': 'Linux'})
        # i_know_what_im_doing launches it anyway.
        launch = options(os='windows', config={'userAgentData.platform': 'Linux'}, i_know_what_im_doing=True)
    assert config_of(launch)['userAgentData.platform'] == 'Linux'


def test_the_reported_core_count_is_the_hosts_unless_pinned(options, monkeypatch):
    from camouwolf import fingerprints

    monkeypatch.setattr(fingerprints, 'host_cpu_count', lambda: 8)
    assert config_of(options(os='windows'))['navigator.hardwareConcurrency'] == 8


LINUX_GPU = sorted(chrome_gpus('linux'))[0]


def test_on_the_hosts_os_the_identity_claims_the_hosts_gpu_and_is_not_farbled(options, monkeypatch):
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: LINUX_GPU)
    with warnings.catch_warnings():
        warnings.simplefilter('error', LeakWarning)
        config = config_of(options(os='linux'))
    assert (config['webGl:vendor'], config['webGl:renderer']) == LINUX_GPU
    assert 'canvas:farblingToken' not in config


@pytest.mark.parametrize('host', [None, ('Google Inc. (Example)', 'ANGLE (Example, unrecorded GPU)')])
def test_a_host_gpu_fpgen_never_recorded_is_farbled(options, monkeypatch, host):
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: host)
    with pytest.warns(LeakWarning, match='farbled'):
        config = config_of(options(os='linux'))
    assert re.fullmatch('[0-9a-f]{32}', config['canvas:farblingToken'])


def test_another_os_is_farbled_with_a_token_per_launch(options, monkeypatch):
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: LINUX_GPU)
    with pytest.warns(LeakWarning, match='farbled'):
        tokens = {config_of(options(os='windows'))['canvas:farblingToken'] for _ in range(2)}
    assert len(tokens) == 2


@pytest.mark.parametrize('canvas_noise', [True, False])
def test_canvas_noise_forces_farbling_without_probing_the_host(options, monkeypatch, canvas_noise):
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: pytest.fail('probed the host GPU'))
    config = config_of(options(os='linux', canvas_noise=canvas_noise))
    assert ('canvas:farblingToken' in config) is canvas_noise


def test_a_display_the_launcher_starts_is_farbled_without_a_probe(options, monkeypatch):
    from camoucore.virtdisplay import VirtualDisplay
    from camouwolf.product import PRODUCT

    display = VirtualDisplay(PRODUCT)
    monkeypatch.setattr(display, 'get', lambda: ':99')
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: pytest.fail('probed before the display started'))
    with pytest.warns(LeakWarning, match='farbled'):
        config = config_of(options(os='linux', headless=False, virtual_display=display))
    assert 'canvas:farblingToken' in config


def test_a_farbled_browsers_contexts_each_get_their_own_token(options):
    launch = options(os='linux', canvas_noise=True)
    tokens = {utils.context_identity(launch, locale='en-US')[0]['canvas:farblingToken'] for _ in range(2)}
    assert config_of(launch)['canvas:farblingToken'] not in tokens and len(tokens) == 2


def test_an_unfarbled_browsers_contexts_claim_its_gpu(options, monkeypatch):
    monkeypatch.setattr(utils, 'host_gpu', lambda *args: LINUX_GPU)
    launch = options(os='linux')
    identity = utils.context_identity(launch, locale='en-US')[0]
    assert (identity['webGl:vendor'], identity['webGl:renderer']) == LINUX_GPU
    assert 'canvas:farblingToken' not in identity
    other = next(gpu for gpu in sorted(chrome_gpus('linux')) if gpu != LINUX_GPU)
    with pytest.raises(ValueError, match='canvas_noise=True'):
        utils.context_identity(launch, locale='en-US', webgl_config=other)


def test_the_old_noise_keys_are_gone(options):
    for key in ('canvas:seed', 'canvas:randomizeReadback', 'AudioContext:randomizeSamples'):
        with pytest.raises(UnknownProperty, match=key):
            options(os='linux', config={key: 1})


# ---- geometry --------------------------------------------------------------


def switch(launch, name):
    return tuple(int(v) for v in next(a for a in launch['args'] if a.startswith(f'--{name}=')).split('=')[1].split(','))


def test_the_window_is_carried_by_switches_and_fits_the_screen(options):
    launch = options(os='windows')
    config = config_of(launch)

    dpr = config['window.devicePixelRatio']
    (x, y), (width, height) = switch(launch, 'window-position'), switch(launch, 'window-size')
    assert chrome_window_reports(x, width, dpr) == (config['window.screenX'], config['window.outerWidth'])
    assert chrome_window_reports(y, height, dpr) == (config['window.screenY'], config['window.outerHeight'])
    assert config['window.outerWidth'] <= config['screen.width']
    assert config['window.outerHeight'] <= config['screen.height']
    assert config['screen.availHeight'] <= config['screen.height']


@pytest.mark.parametrize('seed', [0, 9, 10])  # 9 and 10 draw a fractional ratio
def test_a_fixed_window_size(options, seed):
    # At a fractional ratio the switch is whatever Chrome reports back as the
    # asked size, not the asked size itself.
    random.seed(seed)
    launch = options(os='windows', window=(1200, 800), screen=Screen(min_width=1920, min_height=1080))
    config = config_of(launch)
    dpr = config['window.devicePixelRatio']
    (x, y), (width, height) = switch(launch, 'window-position'), switch(launch, 'window-size')

    assert (config['window.outerWidth'], config['window.outerHeight']) == (1200, 800)
    assert chrome_window_reports(x, width, dpr)[1] == 1200
    assert chrome_window_reports(y, height, dpr)[1] == 800


def test_a_fixed_window_is_kept_when_a_zoomed_ratio_is_repaired(options):
    # The repair rescales the record's CSS sizes to the display's scale; the
    # caller's window is not the record's and stays as asked.
    fingerprint = utils.draw_fingerprint('windows', Screen(min_width=1920, min_height=1080), None, None)
    fingerprint = {**fingerprint, 'window': {**fingerprint['window'], 'devicePixelRatio': 1.1}}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        launch = options(fingerprint=fingerprint, window=(1200, 800))
    assert config_of(launch)['window.devicePixelRatio'] == 1
    assert '--window-size=1200,800' in launch['args']


# Windows Chrome opened at 1.75 on X11, (position, size) asked -> read back.
MEASURED_AT_1_75 = [
    ((0, 1100), (0, 1100)), ((0, 476), (0, 477)), ((0, 1000), (0, 1001)),
    ((0, 480), (0, 481)), ((0, 484), (0, 485)), ((0, 800), (0, 801)),
    ((8, 800), (8, 801)), ((4, 600), (4, 600)), ((0, 1096), (0, 1096)),
    ((0, 616), (0, 616)), ((4, 476), (4, 477)), ((7, 1104), (6, 1106)),
    ((7, 479), (6, 481)),
]


@pytest.mark.parametrize('given, seen', MEASURED_AT_1_75)
def test_the_window_model_is_what_chrome_reported(given, seen):
    assert chrome_window_reports(*given, 1.75) == seen


@pytest.mark.parametrize('dpr', [1.25, 1.5, 1.75, 2.5])
def test_the_claimed_window_is_the_one_chrome_reports_at_a_fractional_ratio(options, dpr):
    fingerprint = utils.draw_fingerprint('windows', Screen(min_width=1920, min_height=1080), None, None)
    # The window a 1.75 profile drew: Chrome opened it and reported 6, 1106.
    window = {**fingerprint['window'], 'devicePixelRatio': dpr, 'screenX': 7, 'screenY': 7,
              'outerWidth': 1103, 'outerHeight': 479}
    launch = options(fingerprint={**fingerprint, 'window': window})
    config = config_of(launch)
    position, size = switch(launch, 'window-position'), switch(launch, 'window-size')

    for axis, (pos_key, size_key) in enumerate((('window.screenX', 'window.outerWidth'),
                                                ('window.screenY', 'window.outerHeight'))):
        claimed = config[pos_key], config[size_key]
        assert chrome_window_reports(position[axis], size[axis], dpr) == claimed, (pos_key, claimed)


# Windows Chrome laid out on X11, (devicePixelRatio, outerWidth) -> innerWidth.
MEASURED_VIEWPORTS = [
    ((1.25, 1200), 1200), ((1.25, 1201), 1202), ((1.25, 1202), 1202), ((1.25, 1205), 1206),
    ((1.25, 1536), 1536), ((1.25, 1537), 1538), ((1.5, 1202), 1202), ((1.75, 1201), 1201),
    ((1.75, 1000), 1000), ((2.5, 1203), 1203), ((3, 1505), 1505),
]


@pytest.mark.parametrize('given, seen', MEASURED_VIEWPORTS)
def test_the_viewport_model_is_what_chrome_reported(given, seen):
    assert chrome_viewport_width(given[1], given[0]) == seen


def test_the_claimed_window_is_never_narrower_than_its_page(options):
    fingerprint = utils.draw_fingerprint('windows', Screen(min_width=1920, min_height=1080), None, None)
    # A windows-1 build-tester profile: Chrome reported it 1537 wide with an innerWidth of 1538.
    window = {**fingerprint['window'], 'devicePixelRatio': 1.25, 'screenX': 2, 'screenY': 0,
              'outerWidth': 1537, 'outerHeight': 832}
    config = config_of(options(fingerprint={**fingerprint, 'window': window}))

    assert chrome_viewport_width(config['window.outerWidth'], 1.25) <= config['window.outerWidth']


def test_a_fixed_window_larger_than_the_screen_is_brought_within_it(options):
    launch = options(os='windows', window=(3000, 2000), screen=Screen(max_width=1920, max_height=1080))
    config = config_of(launch)
    assert config['window.outerWidth'] <= config['screen.availWidth'] <= 1920
    assert config['window.outerHeight'] <= config['screen.availHeight'] <= 1080


def test_screen_constraints_bound_the_draw(options):
    config = config_of(options(os='windows', screen=Screen(max_width=1920, max_height=1080)))
    assert config['screen.width'] <= 1920 and config['screen.height'] <= 1080


def test_a_virtual_display_bounds_the_screen_and_takes_the_window_off_wayland(options):
    launch = options(
        os='windows', headless=False, virtual_display=':77', virtual_display_size=(1600, 900),
        env={'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'},
    )
    config = config_of(launch)

    assert launch['env']['DISPLAY'] == ':77' and 'WAYLAND_DISPLAY' not in launch['env']
    assert '--ozone-platform=x11' in launch['args']
    assert config['screen.width'] <= 1600 and config['screen.height'] <= 900


# ---- locale ----------------------------------------------------------------


def accept_lang(launch):
    return next(arg.split('=', 1)[1].split(',') for arg in launch['args'] if arg.startswith('--accept-lang='))


def test_locale_sets_the_ui_language_and_the_language_pref_together(options):
    launch = options(os='linux', locale=['de-DE', 'en-US'])

    assert accept_lang(launch) == ['de-DE', 'en-US']
    assert '--lang=de-DE' in launch['args']
    assert launch['env']['LANGUAGE'] == 'de_DE'


def test_languages_are_left_to_the_pref_chrome_serves_them_from(options):
    # An off-the-record context swaps the pref's list for Chrome's default; a
    # copy pinned in the config then disagrees with the Accept-Language header.
    assert 'navigator.languages' not in config_of(options(os='linux', locale=['de-DE', 'en-US']))


@pytest.mark.parametrize('locale, expected', [
    ('en-US', ['en-US', 'en']),
    ('de-DE', ['de-DE', 'de', 'en-US', 'en']),
    ('es-ES', ['es-ES', 'es']),
    # Not in Chrome's table: the en-US list follows, as language_prefs.cc does.
    ('ja-JP', ['ja-JP', 'en-US', 'en']),
])
def test_one_locale_claims_chromes_default_list_for_it(options, locale, expected):
    assert accept_lang(options(os='linux', locale=locale)) == expected


def test_every_default_list_is_what_an_off_the_record_profile_serves_for_it():
    lists = utils.CHROME_LANGUAGES['lists']
    for language, default in lists.items():
        assert utils.languages_for({'locale:language': default[0]}) == default, language


def test_a_proxy_without_geoip_warns(options):
    with pytest.warns(LeakWarning, match='geoip'):
        launch = options(os='linux', proxy={'server': 'http://203.0.113.5:8080'})
    assert launch['proxy'] == {'server': 'http://203.0.113.5:8080'}


BERLIN = {'geolocation:latitude': 52.5, 'geolocation:longitude': 13.4, 'timezone': 'Europe/Berlin',
          'locale:language': 'de', 'locale:region': 'DE'}
AMSTERDAM = {'geolocation:latitude': 52.4, 'geolocation:longitude': 4.9, 'timezone': 'Europe/Amsterdam',
             'locale:language': 'nl', 'locale:region': 'NL'}


@pytest.fixture
def places(monkeypatch):
    """Where each IP geolocates, and this host's public IPv6 address (none)."""
    known = {'203.0.113.5': BERLIN}

    class Place:
        def __init__(self, ip):
            if ip not in known:
                raise UnknownIPLocation(ip)
            self.config = known[ip]

        def as_config(self):
            return dict(self.config)

    monkeypatch.setattr(utils, 'geoip_allowed', lambda: None)
    monkeypatch.setattr(utils, 'get_geolocation', lambda ip, geoip_db=None: Place(ip))
    monkeypatch.setattr(utils, 'public_ipv6', lambda: known.get('ipv6'))
    return known


def test_geoip_places_the_browser_at_the_ip(options, places):
    launch = options(os='linux', geoip='203.0.113.5')
    config = config_of(launch)

    assert config['timezone'] == 'Europe/Berlin' and launch['env']['TZ'] == 'Europe/Berlin'
    assert accept_lang(launch) == ['de-DE', 'de', 'en-US', 'en'] and '--lang=de-DE' in launch['args']
    assert utils.launch_geolocation(launch) == {'latitude': 52.5, 'longitude': 13.4}


def test_a_host_without_ipv6_is_an_ipv4_only_network(options, places):
    assert config_of(options(os='linux', geoip='203.0.113.5'))['network:ipv4Only'] is True


def test_ipv6_that_geolocates_with_the_identity_stays_on(options, places):
    places['ipv6'] = '2001:db8::5'
    places['2001:db8::5'] = BERLIN
    assert 'network:ipv4Only' not in config_of(options(os='linux', geoip='203.0.113.5'))


def test_ipv6_that_geolocates_elsewhere_is_turned_off(options, places):
    # An IPv4-only VPN: sites with an IPv6 address would see the host's own.
    places['ipv6'] = '2001:db8::5'
    places['2001:db8::5'] = AMSTERDAM
    with pytest.warns(LeakWarning, match='IPv6'):
        config = config_of(options(os='linux', geoip='203.0.113.5'))
    assert config['network:ipv4Only'] is True


def test_behind_a_proxy_ipv6_is_left_alone(options, places, monkeypatch):
    # Sites are reached only through the proxy, so the host's IPv6 is not seen.
    monkeypatch.setattr(utils, 'public_ipv6', lambda: pytest.fail('looked up the host IPv6 behind a proxy'))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        config = config_of(options(os='linux', geoip='203.0.113.5', proxy={'server': 'http://203.0.113.9:1'}))
    assert 'network:ipv4Only' not in config


def test_behind_a_proxy_webrtc_has_the_exit_address(options, places):
    # A machine at the exit: its one adapter has the exit's address (0031).
    proxy = {'server': 'socks5://203.0.113.9:1'}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        proxied = config_of(options(os='linux', geoip='203.0.113.5', proxy=proxy))
        blocked = config_of(options(os='linux', geoip='203.0.113.5', proxy=proxy, block_webrtc=True))
        direct = config_of(options(os='linux', geoip='203.0.113.5'))
    assert proxied['webrtc:ipv4'] == '203.0.113.5'
    assert 'webrtc:ipv4' not in blocked and 'webrtc:ipv4' not in direct


# ---- switches --------------------------------------------------------------


def blink_settings(args):
    """The settings Chromium reads: the last --blink-settings on the line."""
    found = [a.split('=', 1)[1].split(',') for a in args if a.startswith('--blink-settings=')]
    assert len(found) == 1, found
    return found[0]


def test_a_headless_launch_states_no_pointer_on_the_command_line(options):
    # A command-line pointer answers for every context, so a touchless context
    # of a touch launch would match (any-pointer: coarse); the browser states
    # each context's own (patch 0024).
    launch = options(os='windows', headless=True)
    assert not any(a.startswith('--blink-settings=') for a in launch['args'])
    assert switches.PLAYWRIGHT_HEADLESS_BLINK_SETTINGS in launch['ignore_default_args']
    # Playwright drops every argument equal to an ignored default, the
    # launcher's own included: a mouse alone was dropped, (pointer: none).
    assert not set(launch['args']) & set(launch['ignore_default_args'])


def test_a_headed_launch_leaves_the_pointer_to_the_browser(options):
    launch = options(os='windows', headless=False)
    assert not any(a.startswith('--blink-settings=') for a in launch['args'])
    assert switches.PLAYWRIGHT_HEADLESS_BLINK_SETTINGS not in launch['ignore_default_args']


def test_the_block_options_and_the_cache(options):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        args = options(os='linux', block_images=True, block_webrtc=True, block_webgl=True)['args']
    assert 'imagesEnabled=false' in blink_settings(args)
    assert '--force-webrtc-ip-handling-policy=disable_non_proxied_udp' in args
    assert '--disable-webgl' in args and '--disable-webgl2' in args
    assert '--disk-cache-size=1' in args
    assert '--disk-cache-size=1' not in options(os='linux', enable_cache=True)['args']



def test_the_voices_are_always_sent_so_the_hosts_never_show(options):
    # Patch 0012 shows the host's voices when the key is absent: a record
    # without voices, and a caller's user agent (no draw), must still send it.
    fingerprint = {**utils.draw_fingerprint('windows', None, None, None), 'voices': []}
    assert config_of(options(os=None, fingerprint=fingerprint))['voices'] == []
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        config = config_of(options(os=None, config={'navigator.userAgent': ua}))
    assert config['voices'] == []


POLICY = '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'


def test_only_block_webrtc_takes_webrtc_off_udp(options):
    # A proxied context's UDP goes through the proxy already (0031); the policy
    # would leave a page no UDP at all, which a machine at the exit has.
    proxy = {'server': 'http://127.0.0.1:8080'}
    assert POLICY not in options(os='linux', proxy=proxy)['args']
    assert POLICY not in options(os='linux')['args']
    assert POLICY in options(os='linux', proxy=proxy, block_webrtc=True)['args']


def test_a_conflicting_switch_is_refused_or_dropped_when_asked(options):
    with pytest.raises(ValueError, match="refuses '--user-agent'"):
        options(os='linux', args=['--user-agent=x'])
    launch = options(os='linux', args=['--user-agent=x', '--enable-automation'], i_know_what_im_doing=True)
    assert not [a for a in launch['args'] if a.startswith(('--user-agent', '--enable-automation'))]


# ---- addons ----------------------------------------------------------------


def _extension(path):
    path.mkdir(parents=True)
    (path / 'manifest.json').write_text(json.dumps({'manifest_version': 3, 'name': 'x', 'version': '1'}))
    return str(path)


def test_addons_load_unpacked_and_restate_playwrights_disabled_features(options, tmp_path):
    first, second = _extension(tmp_path / 'a'), _extension(tmp_path / 'b')
    launch = options(os='linux', addons=[first, second])

    assert f'--load-extension={first},{second}' in launch['args']
    disabled = [a for a in launch['args'] if a.startswith('--disable-features=')]
    assert len(disabled) == 1
    features = disabled[0].split('=', 1)[1].split(',')
    assert 'DisableLoadExtensionCommandLineSwitch' in features
    assert set(switches.PLAYWRIGHT_DISABLED_FEATURES) <= set(features)
    assert '--disable-extensions' in launch['ignore_default_args']


def test_every_launch_turns_off_https_first_auto_enable(options):
    # Chromium 158 otherwise upgrades http:// navigations and blocks them when
    # the upgrade fails, as through an HTTP proxy that refuses CONNECT.
    args = options(os='linux')['args']
    assert not [a for a in args if a.startswith('--load-extension')]
    disabled = [a for a in args if a.startswith('--disable-features=')]
    assert len(disabled) == 1
    features = disabled[0].split('=', 1)[1].split(',')
    assert 'HttpsFirstBalancedModeAutoEnable' in features
    assert set(switches.PLAYWRIGHT_DISABLED_FEATURES) <= set(features)
    assert 'DisableLoadExtensionCommandLineSwitch' not in features


def test_a_path_that_is_not_an_unpacked_extension_is_refused(options, tmp_path):
    (tmp_path / 'x.crx').write_bytes(b'')
    (tmp_path / 'empty').mkdir()
    for path in (tmp_path / 'x.crx', tmp_path / 'empty', tmp_path / 'missing'):
        with pytest.raises(InvalidAddonPath):
            options(os='linux', addons=[str(path)])


def test_the_default_addon_is_loaded_when_installed_and_warned_about_when_not(options, tmp_path):
    with pytest.warns(RuntimeWarning, match='camouwolf fetch'):
        launch = options(os='linux', exclude_addons=[])
    assert not [a for a in launch['args'] if a.startswith('--load-extension')]

    installed = _extension(tmp_path / 'cache' / 'addons' / 'UBO')
    launch = options(os='linux', exclude_addons=[])
    assert f'--load-extension={installed}' in launch['args']


def test_the_default_addon_is_pinned_by_version_url_and_sha256():
    pin = addons.addon_pin(DefaultAddons.UBO)
    assert pin['name'] == 'uBlock Origin Lite'
    assert pin['version'] in pin['url'] and pin['url'].endswith('.chromium.zip')
    assert len(pin['sha256']) == 64 and int(pin['sha256'], 16)


def test_fetching_the_default_addon_verifies_and_unpacks_it(tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile

    from camouwolf.exceptions import CorruptedDownload

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('manifest.json', '{"manifest_version": 3}')
    data = archive.getvalue()

    def webdl(url, desc=None, buffer=None, **kwargs):
        buffer.write(data)
        return buffer

    monkeypatch.setattr(addons, 'webdl', webdl)
    monkeypatch.setattr(addons, 'addon_pin', lambda addon: {
        'name': 'x', 'version': '1', 'url': 'https://example.invalid/x.zip', 'sha256': '0' * 64})
    with pytest.raises(CorruptedDownload):
        addons.fetch_default_addons()
    assert not addons.is_installed(DefaultAddons.UBO)

    monkeypatch.setattr(addons, 'addon_pin', lambda addon: {
        'name': 'x', 'version': '1', 'url': 'https://example.invalid/x.zip',
        'sha256': hashlib.sha256(data).hexdigest()})
    (path,) = addons.fetch_default_addons()
    assert (tmp_path / 'cache' / 'addons' / 'UBO' / 'manifest.json').exists() and path.endswith('UBO')
    assert addons.is_installed(DefaultAddons.UBO)


# ---- options without a Chromium equivalent --------------------------------


@pytest.mark.parametrize('option, value, names', [
    ('firefox_user_prefs', {'a.b': 1}, 'chromium_prefs'),
    ('ff_version', 150, 'chrome_version'),
])
def test_an_option_that_is_not_implemented_says_so(options, option, value, names):
    with pytest.raises(NotImplementedError, match=names):
        options(os='linux', **{option: value})


# ---- Camoufox's browser options ------------------------------------------


def disabled_features(launch):
    (switch,) = [a for a in launch['args'] if a.startswith('--disable-features=')]
    return switch.split('=', 1)[1].split(',')


def test_disable_coop_turns_off_the_header_and_warns(options):
    assert 'CrossOriginOpenerPolicy' not in disabled_features(options(os='linux'))
    with pytest.warns(LeakWarning, match='crossOriginIsolated'):
        launch = options(os='linux', disable_coop=True)
    assert 'CrossOriginOpenerPolicy' in disabled_features(launch)


def test_extensions_open_no_tabs_unless_allowed(options):
    assert config_of(options(os='linux'))['allowAddonNewtab'] is False
    assert config_of(options(os='linux', allow_addon_new_tab=True))['allowAddonNewtab'] is True


def test_instant_animations_and_show_cursor_are_opt_in(options):
    config = config_of(options(os='linux'))
    assert 'instantAnimations' not in config and 'showcursor' not in config
    with pytest.warns(LeakWarning, match='duration of zero'):
        config = config_of(options(os='linux', instant_animations=True, show_cursor=True))
    assert config['instantAnimations'] is True and config['showcursor'] is True


def test_debug_logs_to_stderr_and_masks_proxy_credentials(options, capsys):
    proxy = {'server': 'socks5://127.0.0.1:1080', 'username': 'user', 'password': 'hunter2'}
    launch = options(os='linux', debug=True, proxy=proxy)
    assert '--enable-logging=stderr' in launch['args']
    printed = capsys.readouterr().out
    assert "'user'" in printed and 'hunter2' not in printed


# ---- SOCKS5 credentials ----------------------------------------------------


@pytest.mark.parametrize('server, key', [
    ('socks5://Proxy.Example:1081', 'proxy.example:1081'),
    ('socks5://127.0.0.1', '127.0.0.1:1080'),
    ('socks5://[0:0::1]:9050', '[::1]:9050'),
])
def test_socks5_credentials_travel_in_the_config_not_argv(options, server, key):
    proxy = {'server': server, 'username': 'user', 'password': 'p@ss word'}
    launch = options(os='linux', proxy=proxy)
    assert launch['proxy'] == {'server': server}
    assert config_of(launch)['proxy:socks5Credentials'] == {key: {'username': 'user', 'password': 'p@ss word'}}
    assert not any('p@ss word' in arg for arg in launch['args'])


def test_http_proxy_credentials_stay_with_playwright(options):
    proxy = {'server': 'http://127.0.0.1:3128', 'username': 'user', 'password': 'pass'}
    launch = options(os='linux', proxy=proxy)
    assert launch['proxy'] == proxy and 'proxy:socks5Credentials' not in config_of(launch)


def test_socks5_credentials_longer_than_rfc_1929_allows_are_refused(options):
    with pytest.raises(ValueError, match='1 to 255 bytes'):
        options(os='linux', proxy={'server': 'socks5://127.0.0.1:1', 'username': 'u' * 256, 'password': 'p'})


# ---- CA certificates -------------------------------------------------------


def test_certificate_paths_are_read_into_base64_der(options, tmp_path):
    body = 'MIIBszCCAVmgAwIBAgIU' + 'A' * 40
    pem = tmp_path / 'ca.pem'
    pem.write_text(f'-----BEGIN CERTIFICATE-----\n{body[:30]}\n{body[30:]}\n-----END CERTIFICATE-----\n')
    der = tmp_path / 'ca.der'
    der.write_bytes(b'\x30\x82\x01')
    config = config_of(options(os='linux', config={'certificates': ['QUJD'], 'certificatePaths': [str(pem), str(der)]}))
    assert config['certificates'] == ['QUJD', body, 'MIIB']
    assert 'certificatePaths' not in config


# ---- cpu -------------------------------------------------------------------


@pytest.mark.parametrize('os_name, cpu, arch', [
    ('windows', {'manufacturer': 'amd'}, 'x86'),
    ('windows', {'arch': 'x86', 'manufacturer': 'intel'}, 'x86'),
    ('windows', {'arch': 'arm'}, 'arm'),
    ('macos', {'manufacturer': 'apple'}, 'arm'),
    ('macos', {'arch': 'x86'}, 'x86'),
    ('linux', {'manufacturer': 'intel'}, 'x86'),
])
def test_cpu_is_sec_ch_ua_arch_beside_a_gpu_that_machine_has(options, os_name, cpu, arch):
    config = config_of(options(os=os_name, cpu=cpu, allow_os_mismatch=True))
    assert config['userAgentData.architecture'] == arch
    gpu_arch, makers = coherence.gpu_cpu(config['webGl:renderer'], utils.OS_KEYS[os_name])
    assert gpu_arch == arch
    assert not cpu.get('manufacturer') or not makers or cpu['manufacturer'] in makers
    assert not [v for v in coherence.validate(config, utils.OS_KEYS[os_name]) if v.rule == 'architecture']
    assert config.get('cpu:identity') == coherence.cpu_identity(
        config['webGl:renderer'], config['navigator.hardwareConcurrency'], utils.OS_KEYS[os_name], arch,
        cpu.get('manufacturer'))
    assert arch == 'x86' or config['cpu:identity'] == 'arm64'


@pytest.mark.parametrize('machine, cpu, warns', [
    ('aarch64', {'manufacturer': 'intel'}, True),
    ('arm64', {'manufacturer': 'amd'}, True),
    ('arm64', {'arch': 'arm'}, False),
    ('x86_64', {'manufacturer': 'intel'}, False),
])
def test_an_x86_identity_on_an_arm_host_warns(options, monkeypatch, machine, cpu, warns):
    monkeypatch.setattr(utils.platform, 'machine', lambda: machine)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter('always', LeakWarning)
        options(os='windows', cpu=cpu)
    assert any('this host is ARM' in str(w.message) for w in seen) is warns


@pytest.mark.parametrize('os_name, cpu, match', [
    ('linux', {'arch': 'arm'}, 'no Linux identity has an arm CPU'),
    ('macos', {'manufacturer': 'amd'}, "made by 'amd'"),
    ('windows', {'arch': 'arm', 'manufacturer': 'intel'}, 'arm CPU made by'),
    ('windows', {'arch': 'riscv'}, 'arch must be one of'),
    ('windows', {'model': 'x'}, "not 'model'"),
])
def test_a_cpu_no_machine_on_that_os_has_is_refused(options, os_name, cpu, match):
    with pytest.raises(ValueError, match=match):
        options(os=os_name, cpu=cpu, allow_os_mismatch=True)


def test_cpu_narrows_the_os_list_to_those_with_that_cpu(options):
    config = config_of(
        options(os=['windows', 'macos', 'linux'], cpu={'manufacturer': 'apple'}, allow_os_mismatch=True)
    )
    assert utils.get_target_os(config) == 'mac'


def test_cpu_beside_a_gpu_another_cpu_has_is_refused(options):
    apple = next(pair for pair in sorted(webgl.chrome_gpus('macos')) if 'Apple M' in pair[1])
    with pytest.raises(ValueError, match='no machine has'):
        options(os='macos', webgl_config=apple, cpu={'arch': 'x86'}, allow_os_mismatch=True)


# ---- fingerprint_preset -----------------------------------------------------


def chrome_preset(os_name='windows'):
    vendor, renderer = sorted(webgl.chrome_gpus(os_name))[0]
    user_agent = {
        'windows': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/150.0.0.0 Safari/537.36',
        'macos': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                 'Chrome/150.0.0.0 Safari/537.36',
    }[os_name]
    return {
        'navigator': {'userAgent': user_agent, 'maxTouchPoints': 0},
        'screen': {'width': 1920, 'height': 1080, 'colorDepth': 24, 'availWidth': 1920, 'availHeight': 1040},
        'webgl': {'unmaskedVendor': vendor, 'unmaskedRenderer': renderer},
    }


def test_a_chrome_preset_sets_its_os_gpu_and_screen(options):
    preset = chrome_preset()
    config = config_of(options(fingerprint_preset=preset))
    assert utils.get_target_os(config) == 'win'
    assert (config['webGl:vendor'], config['webGl:renderer']) == (
        preset['webgl']['unmaskedVendor'], preset['webgl']['unmaskedRenderer'])
    assert (config['screen.width'], config['screen.height'], config['screen.availHeight']) == (1920, 1080, 1040)
    # The version is the binary's, not the recorded one.
    assert 'Chrome/152.' in config['navigator.userAgent']


def test_a_preset_from_firefox_or_true_is_refused(options):
    preset = chrome_preset()
    preset['navigator']['userAgent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0'
    with pytest.raises(NonChromeFingerprint):
        options(fingerprint_preset=preset)
    with pytest.raises(ValueError, match='bundles none'):
        options(fingerprint_preset=True)
    with pytest.raises(ValueError, match='cannot be combined'):
        options(os='windows', fingerprint_preset=chrome_preset())


def test_firefox_user_prefs_names_args(options):
    with pytest.raises(NotImplementedError, match='`args`'):
        options(firefox_user_prefs={'a': 1})


def test_main_world_eval_is_accepted(options):
    assert options(os='linux', main_world_eval=True)['headless'] is True


def test_chromium_prefs_are_written_to_the_profile(options, tmp_path):
    profile = tmp_path / 'profile'
    (profile / 'Default').mkdir(parents=True)
    (profile / 'Default' / 'Preferences').write_text(json.dumps({'profile': {'name': 'kept'}}))

    launch = options(
        os='linux', user_data_dir=str(profile),
        chromium_prefs={'credentials_enable_service': False, 'profile.password_manager_enabled': False},
    )
    prefs = json.loads((profile / 'Default' / 'Preferences').read_text())

    assert prefs == {
        'credentials_enable_service': False,
        'profile': {'name': 'kept', 'password_manager_enabled': False},
    }
    assert launch['user_data_dir'] == str(profile)


def test_chromium_prefs_without_a_profile_name_args(options):
    with pytest.raises(ValueError, match='persistent_context=True.*`args`'):
        options(os='linux', chromium_prefs={'a': 1})


def test_a_drawn_mac_is_a_machine_apple_made(monkeypatch):
    real = utils.draw_fingerprint('macos', None, None, None)
    apple = 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)'
    record = lambda cores: {**real, 'gpu': {**real['gpu'], 'renderer': apple},
                            'webgl': {**real['webgl'], 'renderer': apple}, 'webgl2': {**real['webgl2'], 'renderer': apple},
                            'navigator': {**real['navigator'], 'hardwareConcurrency': cores}}
    draws = iter([record(5), record(10)])
    monkeypatch.setattr(utils, 'generate_fingerprint', lambda **kwargs: next(draws))
    assert utils.draw_fingerprint('macos', None, None, None)['navigator']['hardwareConcurrency'] == 10


def test_a_drawn_gpu_comes_with_its_own_webgl_readout(monkeypatch):
    # Without one the identity has no device limits for its renderer, and the
    # page reads the host's.
    real = utils.draw_fingerprint('windows', None, None, None)
    unread = {**real, 'webgl2': 'unsupported'}
    draws = iter([unread, real])
    monkeypatch.setattr(utils, 'generate_fingerprint', lambda **kwargs: next(draws))
    assert utils.draw_fingerprint('windows', None, None, None) is real


def test_a_machine_whose_webgl_was_not_recorded_is_not_drawn(monkeypatch):
    # fpgen keeps devices whose WebGL collection failed; an identity drawn from
    # one would claim a GPU with no limits behind it.
    real = utils.draw_fingerprint('linux', None, None, None)
    failed = {**real, 'webgl': 'Failed Collecting: TypeError', 'webgl2': 'Failed Collecting: TypeError'}
    draws = iter([failed, real])
    monkeypatch.setattr(utils, 'generate_fingerprint', lambda **kwargs: next(draws))
    assert utils.draw_fingerprint('linux', None, None, None) is real


def test_the_identity_is_drawn_from_a_machine_in_its_language(options, places, monkeypatch):
    # A machine's voices and fonts follow the language its OS was installed in.
    real = utils.draw_fingerprint('windows', None, None, None)
    asked = []

    def generate(**conditions):
        asked.append(conditions.get('languages'))
        return real

    monkeypatch.setattr(utils, 'generate_fingerprint', generate)
    for kwargs in ({'locale': 'de-DE'}, {'geoip': '203.0.113.5'}):
        asked.clear()
        options(os='windows', **kwargs)
        speaks = asked[0]
        assert speaks and speaks(['de-de', 'de']) and not speaks(['en-us', 'de'])


def test_a_language_fpgen_never_recorded_draws_another_machine(monkeypatch):
    real = utils.draw_fingerprint('windows', None, None, None)

    def generate(**conditions):
        if 'languages' in conditions:
            raise StopIteration  # fpgen's sampler, on a pairing it never recorded
        return real

    monkeypatch.setattr(utils, 'generate_fingerprint', generate)
    assert utils.draw_fingerprint('windows', None, None, None, 'ja-JP') is real
