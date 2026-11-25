import json
import os
import stat
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]

# Tests that start another interpreter test this checkout too, whatever
# camouwolf is installed.
os.environ['PYTHONPATH'] = os.pathsep.join(
    filter(None, (str(PYTHON_DIR / 'src'), os.environ.get('PYTHONPATH')))
)

# The Chromium a test launch claims to be, unless the test says otherwise.
FAKE_VERSION = '152.0.7977.42'
FAKE_RELEASE = 'alpha.1'


def make_bundle(directory: Path, version: str = FAKE_VERSION, release: str = FAKE_RELEASE) -> Path:
    """A browser bundle with no browser in it: the files launch_options reads
    beside the executable. Returns the executable's path."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'version.json').write_text(
        json.dumps({'version': version, 'release': release, 'milestone': int(version.split('.')[0])})
    )
    binary = directory / 'camouwolf'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """No test reaches the real desktop, the network for a browser download, or
    another test's cache: the display variables are removed and the install
    directory is a temporary one."""
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'CAMOUWOLF_EXECUTABLE_PATH', 'CAMOUWOLF_GPU'):
        monkeypatch.delenv(name, raising=False)
    # No test sends telemetry (one with a fake sender turns it back on), and
    # none reads the user's own defaults file.
    monkeypatch.setenv('CAMOUWOLF_TELEMETRY', '0')
    monkeypatch.delenv('DO_NOT_TRACK', raising=False)
    monkeypatch.setenv('CAMOUWOLF_DEFAULTS', str(tmp_path / 'user-defaults.json'))
    from camouwolf import addons, utils

    monkeypatch.setattr(utils, 'INSTALL_DIR', tmp_path / 'cache')
    monkeypatch.setattr(addons, 'ADDONS_DIR', tmp_path / 'cache' / 'addons')
    monkeypatch.setattr(utils, 'warn_if_package_outdated', lambda: None)


@pytest.fixture
def bundle(tmp_path) -> Path:
    """The executable of a fake browser bundle (see make_bundle)."""
    return make_bundle(tmp_path / 'bundle')


@pytest.fixture
def options(bundle):
    """launch_options() against the fake bundle, without the default addon
    (which is installed by `camouwolf fetch`, not present in a test)."""
    from camouwolf import DefaultAddons, launch_options

    def build(**kwargs):
        kwargs.setdefault('executable_path', bundle)
        kwargs.setdefault('exclude_addons', [DefaultAddons.UBO])
        kwargs.setdefault('headless', True)
        return launch_options(**kwargs)

    return build
