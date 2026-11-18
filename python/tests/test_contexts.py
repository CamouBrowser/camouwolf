"""Launching through the API, and what a context of the browser may set."""

import asyncio
import warnings

import pytest

from camouwolf import async_api, sync_api, utils
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


def test_headless_virtual_starts_a_display_and_launches_headed(launch_kwargs, monkeypatch):
    class Display:
        size = (1600, 900)
        killed = False

        def __init__(self, debug=None):
            pass

        def get(self):
            return ':77'

        def kill(self):
            Display.killed = True

    monkeypatch.setattr(utils, 'VirtualDisplay', Display)
    playwright = FakePlaywright()
    launch_kwargs['headless'] = 'virtual'
    browser = sync_api.NewBrowser(playwright, **launch_kwargs)

    (launched,) = playwright.chromium.launched
    assert launched['headless'] is False and launched['env']['DISPLAY'] == ':77'
    assert utils.launch_config(launched)['screen.width'] <= 1600
    browser.close()
    assert Display.killed and playwright.chromium.browser.closed


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
