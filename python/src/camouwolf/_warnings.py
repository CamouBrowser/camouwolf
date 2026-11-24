import platform
from importlib.metadata import PackageNotFoundError, version
from typing import Optional

from camoucore._warnings import LeakWarning, _warn_from_caller  # noqa: F401

from camouwolf.pkgman import load_yaml

WARNINGS_DATA = load_yaml('warnings.yml')


def _browser_version() -> str:
    from camouwolf.exceptions import CamouwolfNotInstalled
    from camouwolf.pkgman import installed_verstr

    try:
        return installed_verstr()
    except CamouwolfNotInstalled:
        return 'not installed'


class FallbackWarning(RuntimeWarning):
    """
    Raised when part of an identity could not be drawn and a substitute was used.
    """

    @staticmethod
    def warn(what: str, instead: str, error: Exception, identity: Optional[str] = None) -> None:
        """
        Warns that `what` failed with `error` and the identity uses `instead`,
        with a block of versions and the error for the user to paste into an issue.
        """
        try:
            camouwolf_version = version('camouwolf')
        except PackageNotFoundError:
            camouwolf_version = 'source checkout'
        lines = [
            f'camouwolf: {camouwolf_version}',
            f'browser: {_browser_version()}',
            f'os: {platform.platform()}',
            f'python: {platform.python_version()}',
            f'error: {type(error).__name__}: {error}',
        ]
        if identity:
            lines.append(f'identity: {identity}')
        report = '\n'.join(f'    {line}' for line in lines)
        _warn_from_caller(
            WARNINGS_DATA['fallback'].format(what=what, instead=instead, report=report),
            FallbackWarning,
        )
