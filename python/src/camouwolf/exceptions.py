from camoucore.exceptions import (  # noqa: F401 -- part of this package's API
    CorruptedDownload,
    UnsupportedOS,
    LocaleError,
    InvalidIP,
    InvalidProxy,
    UnknownIPLocation,
    InvalidLocale,
    UnknownTerritory,
    UnknownLanguage,
    NotInstalledGeoIPExtra,
    VirtualDisplayError,
    CannotFindXvfb,
    CannotExecuteXvfb,
    VirtualDisplayNotSupported,
    FpgenModelError,
)


class UnsupportedVersion(Exception):
    """
    Raised when the Camouwolf executable is outdated.
    """

    ...


class MissingRelease(Exception):
    """
    Raised when a required GitHub release asset is missing.
    """

    ...


class UnsupportedArchitecture(Exception):
    """
    Raised when the architecture is not supported.
    """

    ...


class InvalidPropertyType(Exception):
    """
    Raised when the property type is invalid.
    """

    ...


class InvalidAddonPath(FileNotFoundError):
    """
    Raised when the addon path is invalid.
    """

    ...


class NonChromeFingerprint(Exception):
    """
    Raised when a passed fingerprint is not a Chrome fingerprint.
    """

    ...


class InvalidOS(ValueError):
    """
    Raised when the target OS is invalid.
    """

    ...


class GpuDisplayError(VirtualDisplayError):
    """
    Raised when the headless='virtual-gpu' display cannot be used.
    """

    ...


class NoGpuFound(GpuDisplayError):
    """
    Raised when no GPU render node with a PCI address is found.
    """

    ...


class AmbiguousGpuSelection(GpuDisplayError):
    """
    Raised when several GPUs match, or several exist and none was named.
    """

    ...


class GpuNotFound(GpuDisplayError):
    """
    Raised when the GPU selector matches no GPU.
    """

    ...


class CannotFindXorg(GpuDisplayError):
    """
    Raised when Xorg cannot be found.
    """

    ...


class CannotExecuteXorg(GpuDisplayError):
    """
    Raised when Xorg cannot be started, or exits right after starting.
    """

    ...


class GpuVtTakeover(GpuDisplayError):
    """
    Raised when the Xorg server switched the active console VT.
    """

    ...


class GpuSoftwareFallback(GpuDisplayError):
    """
    Raised when the browser on a GPU display never rendered on the GPU.
    """

    ...


class CamouwolfNotInstalled(FileNotFoundError):
    """
    Raised when camouwolf is not installed.
    """

    ...


class ProfileDirectoryError(RuntimeError):
    """Raised when Camouwolf's required runtime directory cannot be prepared."""

    ...


class UnknownProperty(KeyError):
    """
    Raised when `config` names a property the browser does not read.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ''


class IncoherentIdentity(ValueError):
    """
    Raised when the identity contradicts itself in a way a page can read, such
    as a user agent and client hints naming different operating systems.
    """

    ...


class UnsupportedOSSpoof(ValueError):
    """
    Raised when the identity claims an OS this host cannot pass for.
    """

    ...
