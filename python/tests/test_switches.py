"""Command-line hygiene: what is dropped, what is refused, what is added."""

import pytest

from camouwolf import switches
from camouwolf.switches import audit_switches, merge_list_switches


def test_the_base_switches_are_added_first():
    audit = audit_switches(['--foo'])
    assert audit.args == [*switches.BASE_SWITCHES, '--foo'] and audit.dropped == []


@pytest.mark.parametrize('arg', ['--enable-automation', '--test-type', '--test-type=webdriver', '--use-mock-keychain'])
def test_automation_markers_are_dropped(arg):
    audit = audit_switches([arg])
    assert arg not in audit.args and audit.dropped == [(arg, 'automation marker')]


@pytest.mark.parametrize('arg', ['--disable-web-security', '--ignore-certificate-errors',
                                 '--disable-features=IsolateOrigins,site-per-process'])
def test_switches_a_page_can_measure_are_dropped(arg):
    audit = audit_switches([arg])
    assert arg not in audit.args and audit.dropped == [(arg, 'changes web-visible behavior')]


@pytest.mark.parametrize('arg, option', [
    ('--user-agent=Mozilla/5.0', 'config'),
    ('--lang=de', 'locale'),
    ('--accept-lang=de', 'locale'),
    ('--window-size=800,600', 'window'),
    ('--window-position=0,0', 'config'),
    ('--force-device-scale-factor=2', 'screen'),
    ('--proxy-server=http://x', 'proxy'),
    ('--headless=new', 'headless'),
    ('--user-data-dir=/tmp/x', 'persistent_context'),
])
def test_a_switch_that_contradicts_the_identity_is_refused_naming_the_option(arg, option):
    with pytest.raises(ValueError, match=f"refuses '{arg.split('=')[0]}'") as error:
        audit_switches([arg])
    assert f'`{option}' in str(error.value)

    # Not strict: dropped, with the reason.
    audit = audit_switches([arg], strict=False)
    assert arg not in audit.args and audit.dropped[0][0] == arg


def test_repeated_list_switches_are_merged_because_chromium_reads_the_last():
    merged = merge_list_switches(['--disable-features=A,B', '--x', '--disable-features=B,C', '--enable-features=D'])
    assert merged == ['--disable-features=A,B,C', '--x', '--enable-features=D']


def test_a_caller_blink_feature_joins_the_base_one():
    audit = audit_switches(['--disable-blink-features=Foo'])
    merged = [a for a in audit.args if a.startswith('--disable-blink-features=')]
    assert merged == ['--disable-blink-features=AutomationControlled,Foo']


def test_a_caller_repeat_of_a_base_switch_is_not_doubled():
    audit = audit_switches(['--no-first-run'])
    assert audit.args.count('--no-first-run') == 1


def test_playwright_defaults_that_mark_automation_are_left_out():
    assert {'--enable-automation', '--disable-extensions', '--disable-popup-blocking'} <= set(
        switches.IGNORED_DEFAULT_ARGS
    )


def test_the_restated_playwright_feature_list_matches_the_installed_driver():
    """switches.PLAYWRIGHT_DISABLED_FEATURES must be the list the installed
    Playwright disables, or adding an extension would turn features back on."""
    import re
    from pathlib import Path

    import playwright

    package = Path(playwright.__file__).parent / 'driver' / 'package' / 'lib'
    source = next(
        (text for text in (path.read_text(errors='ignore') for path in package.rglob('*.js'))
         if 'AvoidUnnecessaryBeforeUnloadCheckSync' in text),
        None,
    )
    if source is None:
        pytest.skip('the Playwright driver source does not state its disabled features')
    start = source.index('AvoidUnnecessaryBeforeUnloadCheckSync')
    block = source[start - 200:start + 2500]
    block = block[:block.index(']')]
    found = re.findall(r'"([A-Za-z]+)"', block)
    assert set(found) - {'darwin'} <= set(switches.PLAYWRIGHT_DISABLED_FEATURES)
