"""Extensions: the caller's unpacked ones, and the default uBlock Origin Lite.

Chromium has no Manifest V2, so the default ad blocker is uBlock Origin Lite
(Manifest V3) where Camoufox ships uBlock Origin. It is not vendored and not
part of the browser bundle: `camouwolf fetch` downloads the release pinned in
data/ubo.json (version, url, sha256) and unpacks it under the cache directory.
"""

import io
import os
import shutil
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from zipfile import ZipFile

import orjson

from ._warnings import WARNINGS_DATA, _warn_from_caller
from .exceptions import InvalidAddonPath
from .pkgman import INSTALL_DIR, LOCAL_DATA, verify_sha256, webdl

ADDONS_DIR: Path = INSTALL_DIR / 'addons'


class DefaultAddons(Enum):
    """
    Default addons. `camouwolf fetch` installs them, unpacked, under
    <cache>/addons/<name>/.
    """

    UBO = 'UBO'


# name -> the pin file under data/ that says what to download.
ADDON_PINS: Dict[str, str] = {'UBO': 'ubo.json'}


def addon_pin(addon: DefaultAddons) -> Dict[str, Any]:
    """The pinned release of a default addon: name, version, url, sha256."""
    return orjson.loads((LOCAL_DATA / 'data' / ADDON_PINS[addon.name]).read_bytes())


def confirm_paths(paths: List[str]) -> None:
    """
    Confirms that the addon paths are valid.

    Chromium treats an extension it cannot load as a warning on stderr and
    starts without it, so a mistyped path would otherwise launch a browser that
    just lacks the extension. --load-extension takes an unpacked directory; a
    .crx is ignored.
    """
    for path in paths:
        if not os.path.isdir(path):
            raise InvalidAddonPath(
                f'{path} is not a directory. Addons are unpacked extension directories; '
                'a .crx or .zip is not loaded.'
            )
        if not os.path.exists(os.path.join(path, 'manifest.json')):
            raise InvalidAddonPath(
                f'{path}: manifest.json is missing. Addon path must be a path to an extracted addon.'
            )


def get_addon_path(addon_name: str) -> str:
    """
    Returns the path a default addon is installed at.
    """
    return str(ADDONS_DIR / addon_name)


def is_installed(addon: DefaultAddons) -> bool:
    """Whether the pinned version of a default addon is installed."""
    path = Path(get_addon_path(addon.name))
    try:
        installed = orjson.loads((path / '.camouwolf-addon.json').read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return False
    return (path / 'manifest.json').exists() and installed.get('sha256') == addon_pin(addon)['sha256']


def fetch_default_addons(replace: bool = False) -> List[str]:
    """Download, verify and unpack every default addon. Returns their paths."""
    paths = []
    for addon in DefaultAddons:
        path = Path(get_addon_path(addon.name))
        if replace or not is_installed(addon):
            pin = addon_pin(addon)
            buffer = io.BytesIO()
            webdl(pin['url'], desc=f"Downloading {pin['name']} {pin['version']}", buffer=buffer)
            verify_sha256(buffer, pin['sha256'], desc=pin['name'])
            staging = path.with_name(path.name + '.new')
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            buffer.seek(0)
            with ZipFile(buffer) as archive:
                archive.extractall(staging)  # nosec: verified against the pinned sha256
            (staging / '.camouwolf-addon.json').write_bytes(
                orjson.dumps({'version': pin['version'], 'sha256': pin['sha256']})
            )
            shutil.rmtree(path, ignore_errors=True)
            os.replace(staging, path)
        paths.append(str(path))
    return paths


def add_default_addons(
    addons_list: List[str],
    exclude_list: Optional[List[DefaultAddons]] = None,
) -> None:
    """
    Adds the installed default addons, minus any specified in exclude_list,
    to addons_list
    """
    for addon in DefaultAddons:
        if addon in (exclude_list or []):
            continue
        path = get_addon_path(addon.name)
        # Installed by `camouwolf fetch`. Without it the browser still
        # launches, but not as the browser asked for.
        if not os.path.exists(os.path.join(path, 'manifest.json')):
            _warn_from_caller(
                WARNINGS_DATA['addon_not_installed'].format(addon=addon.name, path=path),
                RuntimeWarning,
            )
            continue
        addons_list.append(path)
