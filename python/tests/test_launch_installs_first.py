"""
launch_options() resolves the browser it will launch before reading its version.

It used to read the version of the active install first and resolve the binary
last. With nothing installed that raised CamouwolfNotInstalled ("run `camouwolf
fetch`") before the download in camouwolf_path() was ever reached (#835), while
the TypeScript launcher downloaded. With an outdated install it generated the
identity for the old Chromium version and then launched the upgraded one.
"""

import pytest

from camouwolf import DefaultAddons, utils
from camouwolf.exceptions import CamouwolfNotInstalled


@pytest.fixture
def nothing_installed(monkeypatch, tmp_path):
    """No active install; launch_path() is where the download happens."""
    binary = tmp_path / "camouwolf-bin"
    downloads = []

    def installed_verstr():
        if not downloads:
            raise CamouwolfNotInstalled("Official is not installed. Please run `camouwolf fetch` to install.")
        return "157.0-beta.34"

    def launch_path(*_args, **_kwargs):
        downloads.append(True)
        return str(binary)

    seen = {}

    def resolve_version(executable_path=None):
        # The version is read from the build that was just resolved.
        seen["resolved_after_download"] = bool(downloads)
        return "157.0.7000.1"

    monkeypatch.setattr(utils, "installed_verstr", installed_verstr)
    monkeypatch.setattr(utils, "launch_path", launch_path)
    monkeypatch.setattr(utils, "resolve_version", resolve_version)
    return downloads, seen, str(binary)


def test_a_first_launch_installs_instead_of_raising(nothing_installed):
    downloads, seen, binary = nothing_installed

    options = utils.launch_options(headless=True, os="linux", exclude_addons=[DefaultAddons.UBO], i_know_what_im_doing=True)

    assert downloads
    assert options["executable_path"] == binary
    assert seen["resolved_after_download"]
    assert "Chrome/157.0.0.0" in utils.launch_config(options)["navigator.userAgent"]
