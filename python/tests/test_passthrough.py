"""passthrough=True: the host's own fingerprint, with the behaviour options kept."""

import warnings

import pytest

from camouwolf import sync_api, utils
from camouwolf._warnings import LeakWarning



class FakeBrowser:
    def __init__(self):
        self.contexts = []

    def new_context(self, **kwargs):
        self.contexts.append(kwargs)


class FakePlaywright:
    def __init__(self):
        self.chromium = self
        self.browser = FakeBrowser()

    def launch(self, **kwargs):
        return self.browser


@pytest.fixture(autouse=True)
def linux_host(monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')


def test_no_identity_is_sent(options):
    launch = options(
        passthrough=True, block_webrtc=True, proxy={'server': 'http://proxy.example:8080'}, i_know_what_im_doing=True
    )

    config = utils.launch_config(launch)
    assert not [key for key in config if '.' in key], config
    assert not [arg for arg in launch['args'] if arg.startswith(('--lang', '--accept-lang', '--window-'))]
    assert not {'FONTCONFIG_FILE', 'TZ', 'LANGUAGE'} & set(launch['env'])
    # The behaviour options still apply.
    assert launch['proxy'] == {'server': 'http://proxy.example:8080'}
    assert utils.WEBRTC_BLOCK_SWITCH in launch['args']
    assert utils.launch_is_passthrough(launch)


def test_a_drawn_launch_is_not_passthrough(options):
    assert not utils.launch_is_passthrough(options(os='linux'))


@pytest.mark.parametrize('option, value', [
    ('os', 'windows'),
    ('locale', 'de-DE'),
    ('geoip', True),
    ('config', {'timezone': 'Europe/Berlin'}),
    ('canvas_noise', True),
])
def test_an_identity_option_alongside_it_raises(options, option, value):
    with pytest.raises(ValueError, match=option):
        options(passthrough=True, **{option: value})


def test_a_proxy_warns_that_the_host_shows(options):
    with pytest.warns(LeakWarning, match='passthrough'):
        options(passthrough=True, proxy={'server': 'http://proxy.example:8080'})


def test_contexts_keep_the_hosts_identity(options):
    playwright = FakePlaywright()
    browser = sync_api.NewBrowser(playwright, from_options=options(passthrough=True))

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        browser.new_context(locale='de-DE')
    assert browser.contexts[0]['no_viewport'] is True
    assert 'geolocation' not in browser.contexts[0]

    with pytest.raises(ValueError, match='passthrough'):
        sync_api.NewContext(browser)
