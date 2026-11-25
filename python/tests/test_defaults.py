"""camouwolf.defaults.json: the defaults every launch starts from, and the user's own."""

import inspect
import json

import pytest
from click.testing import CliRunner

from camouwolf import __main__ as cli
from camouwolf import utils


def documented(path=utils.DEFAULTS_FILE):
    return {name: entry for name, entry in json.loads(path.read_text()).items() if not name.startswith('$')}


def test_every_default_is_a_launch_option():
    parameters = inspect.signature(utils.launch_options.__wrapped__).parameters
    assert set(documented()) <= set(parameters)


def test_every_defaulted_option_is_unset_until_the_file_sets_it():
    # A parameter default other than None would win over the file.
    parameters = inspect.signature(utils.launch_options.__wrapped__).parameters
    for name, entry in documented().items():
        assert parameters[name].default is None, name
        assert entry['about'] and entry['examples'], name


def test_a_user_default_applies_to_an_unset_option(options, tmp_path, monkeypatch):
    user = tmp_path / 'mine.json'
    user.write_text(json.dumps({'block_images': True}))
    monkeypatch.setenv('CAMOUWOLF_DEFAULTS', str(user))

    with pytest.warns(Warning):
        assert '--blink-settings=imagesEnabled=false' in options(os='linux')['args']


def test_an_explicit_option_wins_over_the_users_default(options, tmp_path, monkeypatch):
    user = tmp_path / 'mine.json'
    user.write_text(json.dumps({'block_images': True, 'os': 'windows'}))
    monkeypatch.setenv('CAMOUWOLF_DEFAULTS', str(user))

    launch = options(os='linux', block_images=False)
    assert '--blink-settings=imagesEnabled=false' not in launch['args']
    assert 'Linux' in utils.launch_config(launch)['navigator.userAgent']


def test_a_typo_in_the_users_file_is_an_error(options, tmp_path, monkeypatch):
    user = tmp_path / 'mine.json'
    user.write_text(json.dumps({'block_imges': True}))
    monkeypatch.setenv('CAMOUWOLF_DEFAULTS', str(user))

    with pytest.raises(ValueError, match='block_imges'):
        options(os='linux')


def test_the_defaults_command_writes_the_users_copy(tmp_path, monkeypatch):
    user = tmp_path / 'config' / 'defaults.json'
    monkeypatch.setenv('CAMOUWOLF_DEFAULTS', str(user))

    result = CliRunner().invoke(cli.cli, ['defaults'])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(user)
    assert user.read_text() == utils.DEFAULTS_FILE.read_text()
