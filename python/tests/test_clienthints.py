"""The user agent and the client hints come from one identity and must agree."""

import pytest

from camouwolf import clienthints as ch


@pytest.mark.parametrize('os_key, token, platform, ch_platform', [
    ('win', 'Windows NT 10.0; Win64; x64', 'Win32', 'Windows'),
    ('mac', 'Macintosh; Intel Mac OS X 10_15_7', 'MacIntel', 'macOS'),
    ('lin', 'X11; Linux x86_64', 'Linux x86_64', 'Linux'),
])
def test_one_identity_gives_agreeing_values(os_key, token, platform, ch_platform):
    config = ch.client_hint_config(ch.ChromeIdentity(milestone=152, full_version='152.0.7977.42', os=os_key))

    assert config['navigator.userAgent'] == (
        f'Mozilla/5.0 ({token}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'
    )
    assert config['navigator.platform'] == platform
    assert config['userAgentData.platform'] == ch_platform
    assert config['userAgentData.uaFullVersion'] == '152.0.7977.42'
    assert config['userAgentData.platformVersion'] in ch.PLATFORM_VERSIONS[os_key]
    assert config['userAgentData.architecture'] == ('arm' if os_key == 'mac' else 'x86')
    assert config['userAgentData.model'] == '' and config['userAgentData.mobile'] is False
    assert ch.ua_os(config['navigator.userAgent']) == os_key
    assert ch.assert_coherent(config) == []


def test_the_user_agent_carries_the_reduced_version_only():
    ua = ch.build_user_agent(ch.ChromeIdentity(152, '152.0.7977.42', 'win'))
    assert 'Chrome/152.0.0.0' in ua and '7977' not in ua


def test_a_recorded_user_agent_is_moved_to_the_running_milestone():
    recorded = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
    assert 'Chrome/152.0.0.0 Safari' in ch.retarget_user_agent(recorded, 152)


def test_the_launch_leaves_the_brand_list_to_chromium():
    # The public build claims Chromium: Chromium builds its own brand list, and
    # "Google Chrome" in it is a Pro feature.
    config = {}
    ch.fill_client_hints(config, 'win', '158.0.8127.36')
    assert not any(k in config for k in ('userAgentData.brands', 'userAgentData.fullVersionList'))


def test_fill_keeps_what_the_config_already_holds():
    config = {'navigator.userAgent': 'x', 'userAgentData.platformVersion': '19.0.0'}
    ch.fill_client_hints(config, 'win', '152.0.7977.42')

    assert config['navigator.userAgent'] == 'x'
    assert config['userAgentData.platformVersion'] == '19.0.0'
    assert config['userAgentData.platform'] == 'Windows'


def _windows():
    return ch.client_hint_config(ch.ChromeIdentity(152, '152.0.7977.42', 'win'))


@pytest.mark.parametrize('change, problem', [
    ({'userAgentData.platform': 'Linux'}, 'Sec-CH-UA-Platform'),
    ({'navigator.platform': 'MacIntel'}, 'navigator.platform'),
    ({'userAgentData.platformVersion': '11.0.0'}, 'Platform-Version'),
    ({'userAgentData.mobile': True}, 'Mobile'),
    ({'userAgentData.wow64': True}, 'WoW64'),
    ({'userAgentData.model': 'Pixel 9'}, 'Model'),
    ({'userAgentData.uaFullVersion': '151.0.1.2'}, 'Full-Version'),
])
def test_each_contradiction_is_reported(change, problem):
    problems = ch.assert_coherent({**_windows(), **change})
    assert len(problems) == 1 and problem in problems[0]


def test_a_config_without_a_user_agent_has_nothing_to_contradict():
    assert ch.assert_coherent({'userAgentData.platform': 'Linux'}) == []
