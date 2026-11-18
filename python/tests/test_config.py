"""The config contract: the schema, validation, and how it reaches the browser."""

import json
from pathlib import Path

import orjson
import pytest

from camouwolf import utils
from camouwolf.exceptions import InvalidPropertyType, UnknownProperty

SCHEMA = Path(utils.PROPERTIES_FILE)
TYPES = {'str', 'int', 'uint', 'double', 'bool', 'array', 'dict'}


def test_the_schema_is_a_list_of_typed_properties_without_repeats():
    entries = json.loads(SCHEMA.read_text())
    names = [entry['property'] for entry in entries]

    assert len(names) == len(set(names))
    assert {entry['type'] for entry in entries} <= TYPES
    assert all(set(entry) <= {'property', 'type', 'carriedBy'} for entry in entries)
    # What the launcher carries itself is marked, so the browser's copy can drop it.
    carried = {entry['property'] for entry in entries if entry.get('carriedBy') == 'launcher'}
    assert carried == {
        'window.outerWidth', 'window.outerHeight', 'window.screenX', 'window.screenY',
        'geolocation:latitude', 'geolocation:longitude', 'geolocation:accuracy',
    }


def test_an_unknown_key_raises_unknown_property():
    with pytest.raises(UnknownProperty, match="navigator.oscpu"):
        utils.validate_config({'navigator.oscpu': 'Linux x86_64'})
    assert issubclass(UnknownProperty, KeyError)


def test_a_launch_refuses_an_unknown_key(options):
    with pytest.raises(UnknownProperty, match='humanize'):
        options(os='linux', config={'humanize': True}, i_know_what_im_doing=True)


@pytest.mark.parametrize('key, value', [
    ('navigator.userAgent', 5),
    ('navigator.hardwareConcurrency', -1),
    ('navigator.hardwareConcurrency', '8'),
    ('navigator.hardwareConcurrency', True),
    ('navigator.deviceMemory', '8'),
    ('screen.width', 1920.5),
    ('userAgentData.mobile', 0),
    ('navigator.languages', 'en-US'),
    ('navigator.languages', ['en-US', 5]),
    ('fonts', {'Arial': True}),
    ('systemColors', ['canvas']),
    ('systemColors', {'canvas': '#ffffff'}),
    ('webGl:parameters', {'MAX_TEXTURE_SIZE': 16384}),
    ('webGl:parameters', {'3379': [16384]}),
])
def test_a_value_of_the_wrong_type_is_refused(key, value):
    with pytest.raises(InvalidPropertyType):
        utils.validate_config({key: value})


def test_values_of_the_right_type_pass():
    utils.validate_config({
        'navigator.userAgent': 'x', 'navigator.hardwareConcurrency': 8, 'navigator.deviceMemory': 0.5,
        'screen.availLeft': -1920, 'userAgentData.mobile': False, 'navigator.languages': ['en-US'],
        'systemColors': {'canvas': 0xFFFFFF}, 'webGl:parameters': {'3379': 16384, '7936': 'WebKit'},
        'storage.quota': 1e12, 'timezone': 'Europe/Berlin',
    })


def test_a_build_that_ships_its_own_schema_is_validated_against_it(bundle):
    (bundle.parent / 'properties.json').write_text(json.dumps([{'property': 'only.this', 'type': 'str'}]))

    utils.validate_config({'only.this': 'x'}, path=bundle)
    with pytest.raises(UnknownProperty):
        utils.validate_config({'navigator.userAgent': 'x'}, path=bundle)


# ---- the environment --------------------------------------------------------


def _join(env):
    keys = sorted((k for k in env if k.startswith('CAMOU_CONFIG_')), key=lambda k: int(k.rsplit('_', 1)[1]))
    return ''.join(env[k] for k in keys), keys


def test_a_small_config_is_one_variable():
    env = utils.config_env_vars({'navigator.userAgent': 'x'})
    assert list(env) == ['CAMOU_CONFIG_1'] and orjson.loads(env['CAMOU_CONFIG_1']) == {'navigator.userAgent': 'x'}


@pytest.mark.parametrize('chunk_size', [2047, 32767])
def test_a_large_config_is_split_and_joins_back(chunk_size):
    config = {'fonts': [f'Font Family {i}' for i in range(6000)], 'navigator.userAgent': 'é' * 50}
    env = utils.config_env_vars(config, chunk_size=chunk_size)
    blob, keys = _join(env)

    assert keys == [f'CAMOU_CONFIG_{i}' for i in range(1, len(keys) + 1)] and len(keys) > 1
    assert all(len(env[k]) <= chunk_size for k in keys)
    assert orjson.loads(blob) == config


def test_windows_splits_at_2047(monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'win')
    env = utils.config_env_vars({'fonts': ['x' * 100] * 100})
    assert max(len(v) for v in env.values()) == 2047


def test_launch_config_reads_back_what_the_launch_carries(options):
    launch = options(os='windows')
    config = utils.launch_config(launch)

    assert config['navigator.platform'] == 'Win32'
    assert utils.launch_os(launch) == 'windows'
    assert utils.launch_config({'env': {}}) == {}


def test_the_identity_is_never_on_the_command_line(options):
    launch = options(os='windows', locale='de-DE')
    config = utils.launch_config(launch)
    argv = ' '.join(launch['args'])

    assert config['navigator.userAgent'] not in argv and 'Mozilla' not in argv
    assert config['webGl:renderer'] not in argv
    assert not any(arg.startswith('--user-agent') for arg in launch['args'])


def test_a_windows_identity_on_another_host_asks_for_ucrt_math(monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')
    assert utils.get_env_vars({}, 'win')['CAMOUWOLF_UCRT_MATH'] == '1'
    assert 'CAMOUWOLF_UCRT_MATH' not in utils.get_env_vars({}, 'lin')
    monkeypatch.setattr(utils, 'OS_NAME', 'win')
    assert 'CAMOUWOLF_UCRT_MATH' not in utils.get_env_vars({}, 'win')


def test_the_timezone_is_also_the_process_zone(monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')
    assert utils.get_env_vars({'timezone': 'Europe/Berlin'}, 'lin')['TZ'] == 'Europe/Berlin'
    assert 'TZ' not in utils.get_env_vars({}, 'lin')


def test_the_bundle_fontconfig_is_rewritten_with_absolute_paths(bundle, monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')
    root = bundle.parent
    (root / 'fontconfig' / 'windows').mkdir(parents=True)
    (root / 'fontconfig' / 'windows' / 'fonts.conf').write_text('<fontconfig><dir prefix="cwd">fonts</dir></fontconfig>')
    # No font files yet: loading the config would leave the browser with none.
    assert 'FONTCONFIG_FILE' not in utils.get_env_vars({}, 'win', path=bundle)

    (root / 'fonts' / 'windows').mkdir(parents=True)
    (root / 'fonts' / 'linux').mkdir(parents=True)
    (root / 'fonts' / 'windows' / 'arial.ttf').write_bytes(b'')
    conf = Path(utils.get_env_vars({}, 'win', path=bundle)['FONTCONFIG_FILE'])

    text = conf.read_text()
    assert f"<dir>{root / 'fonts' / 'windows'}</dir>" in text and 'prefix="cwd"' not in text
    # Never the parent: the other OS's fonts would become fallback candidates.
    assert f"<dir>{root / 'fonts'}</dir>" not in text
    assert root not in conf.parents
