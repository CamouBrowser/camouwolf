"""Launching through the API, and what a context of the browser may set."""

import asyncio
import math
import warnings

import pytest

from camouwolf import async_api, coherence, sync_api, utils
from camouwolf._warnings import LeakWarning
from camouwolf.exceptions import IncoherentIdentity


class FakeContext:
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def close(self):
        pass


class FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        self.contexts.append(kwargs)
        return FakeContext(kwargs)

    def new_page(self, **kwargs):
        self.contexts.append(kwargs)
        return FakeContext(kwargs)

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self):
        self.launched = []
        self.persistent = []
        self.browser = FakeBrowser()

    def launch(self, **kwargs):
        self.launched.append(kwargs)
        return self.browser

    def launch_persistent_context(self, **kwargs):
        self.persistent.append(kwargs)
        return self.browser


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()

    @property
    def firefox(self):
        raise AssertionError('Camouwolf launches Chromium')


@pytest.fixture
def launch_kwargs(bundle):
    from camouwolf import DefaultAddons

    return {'executable_path': bundle, 'exclude_addons': [DefaultAddons.UBO], 'os': 'linux', 'headless': True}


def test_new_browser_launches_chromium_with_the_launch_options(launch_kwargs):
    playwright = FakePlaywright()
    browser = sync_api.NewBrowser(playwright, **launch_kwargs)

    (launched,) = playwright.chromium.launched
    assert browser is playwright.chromium.browser
    assert launched['executable_path'] == str(launch_kwargs['executable_path'])
    assert '--enable-automation' in launched['ignore_default_args']
    assert any(key.startswith('CAMOU_CONFIG_') for key in launched['env'])


def test_from_options_are_launched_as_given(options):
    playwright = FakePlaywright()
    built = options(os='windows')
    sync_api.NewBrowser(playwright, from_options=built)
    assert playwright.chromium.launched == [built]


def test_a_context_reads_the_real_window_and_the_hosts_media_features(launch_kwargs):
    browser = sync_api.NewBrowser(FakePlaywright(), **launch_kwargs)
    browser.new_context()
    browser.new_page(viewport={'width': 800, 'height': 600})

    first, second = browser.contexts
    # Playwright's default viewport and media features are emulation overrides.
    assert first['no_viewport'] is True and first['color_scheme'] == 'no-override'
    assert first['reduced_motion'] == first['forced_colors'] == first['contrast'] == 'no-override'
    # A caller's viewport is the caller's.
    assert second['viewport'] == {'width': 800, 'height': 600} and 'no_viewport' not in second


def test_the_launchs_geolocation_is_granted_to_every_context(launch_kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        browser = sync_api.NewBrowser(
            FakePlaywright(), config={'geolocation:latitude': 1.5, 'geolocation:longitude': 2.5}, **launch_kwargs
        )
    browser.new_context(permissions=['notifications'])

    (context,) = browser.contexts
    assert context['geolocation'] == {'latitude': 1.5, 'longitude': 2.5}
    assert context['permissions'] == ['notifications', 'geolocation']


def test_a_persistent_context_gets_the_defaults_in_its_launch(launch_kwargs, tmp_path):
    playwright = FakePlaywright()
    sync_api.NewBrowser(playwright, persistent_context=True, user_data_dir=str(tmp_path / 'p'), **launch_kwargs)

    (launched,) = playwright.chromium.persistent
    assert launched['user_data_dir'] == str(tmp_path / 'p')
    assert launched['no_viewport'] is True and launched['color_scheme'] == 'no-override'
    assert not playwright.chromium.launched


def test_a_context_option_that_changes_the_identity_warns(launch_kwargs):
    browser = sync_api.NewBrowser(FakePlaywright(), **launch_kwargs)
    with pytest.warns(LeakWarning, match='user_agent= cannot be set per context'):
        browser.new_context(user_agent='Mozilla/5.0 other')


@pytest.mark.parametrize('option, value', [
    ('user_agent', 'Mozilla/5.0 other'), ('is_mobile', True), ('device_scale_factor', 2),
    ('screen', {'width': 800, 'height': 600}), ('has_touch', True),
])
def test_new_context_refuses_an_identity_of_its_own(option, value):
    browser = FakeBrowser()
    with pytest.raises(IncoherentIdentity, match=option):
        sync_api.NewContext(browser, **{option: value})
    assert browser.contexts == []


@pytest.mark.parametrize('option', ['preset', 'os', 'ff_version', 'webrtc_ip'])
def test_new_context_does_not_draw_a_second_fingerprint(option):
    with pytest.raises(NotImplementedError, match=option):
        sync_api.NewContext(FakeBrowser(), **{option: 'x'})


def test_new_context_places_a_context(launch_kwargs):
    browser = FakeBrowser()
    sync_api.NewContext(
        browser, proxy={'server': 'http://203.0.113.5:1'}, geolocation={'latitude': 1, 'longitude': 2},
        timezone_id='Europe/Berlin', locale='de-DE',
    )
    assert browser.contexts == [{
        'proxy': {'server': 'http://203.0.113.5:1'}, 'geolocation': {'latitude': 1, 'longitude': 2},
        'permissions': ['geolocation'], 'timezone_id': 'Europe/Berlin', 'locale': 'de-DE',
    }]



@pytest.fixture
def exit_lookup(monkeypatch):
    """The proxy exits at 198.51.100.7, in Tokyo; records each lookup."""
    from camoucore.locales import Geolocation, Locale

    looked_up = []

    def public_ip(proxy=None):
        looked_up.append(proxy)
        return '198.51.100.7'

    monkeypatch.setattr(utils, 'geoip_allowed', lambda: None)
    monkeypatch.setattr(utils, 'public_ip', public_ip)
    monkeypatch.setattr(utils, 'get_geolocation', lambda ip, geoip_db=None: Geolocation(
        locale=Locale(language='ja', region='JP'), longitude=139.7, latitude=35.7, timezone='Asia/Tokyo'))
    return looked_up


PROXY = {'server': 'http://203.0.113.5:1', 'username': 'u', 'password': 'p'}


def test_a_proxied_context_is_placed_where_the_proxy_exits(exit_lookup):
    # Otherwise the context keeps the launch's timezone and position beside the
    # proxy's IP.
    class AsyncBrowser(FakeBrowser):
        async def new_context(self, **kwargs):
            return super().new_context(**kwargs)

    browser, async_browser = FakeBrowser(), AsyncBrowser()
    sync_api.NewContext(browser, proxy=PROXY)
    asyncio.run(async_api.AsyncNewContext(async_browser, proxy=PROXY, timezone_id='Asia/Osaka'))
    assert exit_lookup == ['http://u:p@203.0.113.5:1'] * 2
    assert browser.contexts + async_browser.contexts == [
        {'proxy': PROXY, 'geolocation': {'latitude': 35.7, 'longitude': 139.7},
         'permissions': ['geolocation'], 'timezone_id': 'Asia/Tokyo'},
        {'proxy': PROXY, 'geolocation': {'latitude': 35.7, 'longitude': 139.7},
         'permissions': ['geolocation'], 'timezone_id': 'Asia/Osaka'},
    ]


def test_a_failed_exit_lookup_raises(exit_lookup, monkeypatch):
    from camouwolf.exceptions import InvalidIP

    def unreachable(proxy=None):
        raise InvalidIP('Failed to get IP address')

    monkeypatch.setattr(utils, 'public_ip', unreachable)
    browser = FakeBrowser()
    with pytest.raises(InvalidIP):
        sync_api.NewContext(browser, proxy=PROXY)
    assert browser.contexts == []


def test_the_async_api_launches_the_same_way(launch_kwargs):
    class AsyncChromium(FakeChromium):
        async def launch(self, **kwargs):
            self.launched.append(kwargs)
            return self.browser

    playwright = FakePlaywright()
    playwright.chromium = AsyncChromium()
    browser = asyncio.run(async_api.AsyncNewBrowser(playwright, **launch_kwargs))
    browser.new_context()

    assert len(playwright.chromium.launched) == 1
    assert browser.contexts[0]['no_viewport'] is True


class FakeDisplay(utils.VirtualDisplay):
    """A display that records the size it was started at instead of running Xvfb."""

    started = None
    killed = False

    def get(self):
        FakeDisplay.started = self.size
        self.proc = object()
        return ':77'

    def kill(self):
        FakeDisplay.killed = True


@pytest.fixture
def fake_display(monkeypatch):
    monkeypatch.setattr(utils, 'VirtualDisplay', FakeDisplay)
    monkeypatch.setattr(FakeDisplay, 'started', None)
    monkeypatch.setattr(FakeDisplay, 'killed', False)
    return FakeDisplay


def test_headless_virtual_starts_a_display_and_launches_headed(launch_kwargs, fake_display, monkeypatch):
    monkeypatch.setenv('CAMOUWOLF_VIRTUAL_DISPLAY_SIZE', '1600x900')
    playwright = FakePlaywright()
    launch_kwargs['headless'] = 'virtual'
    browser = sync_api.NewBrowser(playwright, **launch_kwargs)

    (launched,) = playwright.chromium.launched
    config = utils.launch_config(launched)
    assert launched['headless'] is False and launched['env']['DISPLAY'] == ':77'
    assert fake_display.started == (1600, 900)
    # A fixed display holds the screen in device pixels.
    assert config['screen.width'] * config['window.devicePixelRatio'] <= 1600
    browser.close()
    assert fake_display.killed and playwright.chromium.browser.closed


@pytest.mark.parametrize('draw', range(8))
def test_headless_virtual_display_is_the_claimed_screen_at_its_ratio(launch_kwargs, fake_display, monkeypatch, draw):
    """The window renders at the identity's devicePixelRatio, so the display the
    launcher owns is the claimed screen in device pixels, and a high-ratio
    identity's screen is not cut down to fit a fixed-size display."""
    monkeypatch.delenv('CAMOUWOLF_VIRTUAL_DISPLAY_SIZE', raising=False)
    playwright = FakePlaywright()
    launch_kwargs.update(headless='virtual', os='windows')
    sync_api.NewBrowser(playwright, **launch_kwargs)

    config = utils.launch_config(playwright.chromium.launched[0])
    dpr = config['window.devicePixelRatio']
    assert dpr in coherence.PLAUSIBLE_DPR['win']
    assert fake_display.started == (
        math.ceil(config['screen.width'] * dpr), math.ceil(config['screen.height'] * dpr)
    )


def test_addons_on_a_non_persistent_launch_warn_that_they_will_not_run(options, tmp_path):
    extension = tmp_path / 'ext'
    extension.mkdir()
    (extension / 'manifest.json').write_text('{}')
    built = options(os='linux', addons=[str(extension)])

    with pytest.warns(RuntimeWarning, match='persistent_context=True'):
        sync_api.NewBrowser(FakePlaywright(), from_options=built)
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        sync_api.NewBrowser(
            FakePlaywright(), from_options={**built, 'user_data_dir': str(tmp_path / 'p')}, persistent_context=True
        )
