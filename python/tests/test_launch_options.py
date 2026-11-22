"""launch_options(): the options, and what each one turns into."""

import json
import random
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
    UnsupportedOSSpoof,
)
from camouwolf.fingerprints import Screen, chrome_viewport_width, chrome_window_reports


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


def test_stock_chrome_canvas_by_default_and_noise_on_request(options):
    config = config_of(options(os='linux'))
    assert config['canvas:seed'] == 0 and config['canvas:randomizeReadback'] is False

    with pytest.warns(LeakWarning, match='canvas_noise'):
        noisy = config_of(options(os='linux', canvas_noise=True))
    assert noisy['canvas:seed'] > 0 and noisy['canvas:randomizeReadback'] is True


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


@pytest.mark.parametrize('touch_points, pointers', [(10, '6'), (0, '4')])
def test_a_headless_touch_identity_has_a_coarse_pointer_beside_its_mouse(options, touch_points, pointers):
    # Playwright's headless launch states a mouse alone, which on a touch
    # identity leaves (any-pointer: coarse) unmatched beside maxTouchPoints 10.
    fingerprint = utils.draw_fingerprint('windows', Screen(min_width=1920, min_height=1080), None, None)
    fingerprint = {**fingerprint, 'navigator': {**fingerprint['navigator'], 'maxTouchPoints': touch_points}}
    launch = options(fingerprint=fingerprint, headless=True)

    assert config_of(launch)['navigator.maxTouchPoints'] == touch_points
    assert f'availablePointerTypes={pointers}' in blink_settings(launch['args'])
    assert switches.PLAYWRIGHT_HEADLESS_BLINK_SETTINGS in launch['ignore_default_args']


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
    ('cpu', {'arch': 'arm'}, 'cpu'),
    ('fingerprint_preset', True, 'fingerprint_preset'),
    ('disable_coop', True, 'disable_coop'),
    ('allow_addon_new_tab', True, 'allow_addon_new_tab'),
])
def test_an_option_that_is_not_implemented_says_so(options, option, value, names):
    with pytest.raises(NotImplementedError, match=names):
        options(os='linux', **{option: value})


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
                            'navigator': {**real['navigator'], 'hardwareConcurrency': cores}}
    draws = iter([record(5), record(10)])
    monkeypatch.setattr(utils, 'generate_fingerprint', lambda **kwargs: next(draws))
    assert utils.draw_fingerprint('macos', None, None, None)['navigator']['hardwareConcurrency'] == 10


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
