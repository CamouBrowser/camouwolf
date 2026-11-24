/**
 * TypeScript twin of python/src/camouwolf/exceptions.py.
 *
 * The Python hierarchy leans on builtin bases (FileNotFoundError, ValueError,
 * ImportError) that have no JS analogue; those become plain Error subclasses
 * here. Every relationship that callers actually catch on -- LocaleError,
 * VirtualDisplayError -- is preserved.
 */

// The core's own errors, so callers catch one class whichever side raised it.
export {
	CannotExecuteXvfb,
	CannotFindXvfb,
	CorruptedDownload,
	InvalidIP,
	InvalidLocale,
	InvalidProxy,
	LocaleError,
	NotInstalledGeoIPExtra,
	UnknownIPLocation,
	UnknownLanguage,
	UnknownTerritory,
	UnsupportedOS,
	VirtualDisplayError,
	VirtualDisplayNotSupported,
} from "camoucore/exceptions.js";

import { VirtualDisplayError } from "camoucore/exceptions.js";

/** The Python twin relies on the builtin FileNotFoundError; JS has no such
 *  class, so version-lookup misses raise this instead. */
export class FileNotFoundError extends Error {
	constructor(message?: string) {
		super(message ?? "File couldn't be found.");
		this.name = "FileNotFoundError";
	}
}

export class UnsupportedVersion extends Error {
	constructor(message?: string) {
		super(message ?? "The Camouwolf executable is outdated.");
		this.name = "UnsupportedVersion";
	}
}

export class MissingRelease extends Error {
	constructor(message?: string) {
		super(message ?? "A required GitHub release asset is missing.");
		this.name = "MissingRelease";
	}
}

/** Raised when a downloaded asset does not match its expected sha256 digest. */
export class UnsupportedArchitecture extends Error {
	constructor(message?: string) {
		super(message ?? "The architecture is not supported.");
		this.name = "UnsupportedArchitecture";
	}
}

export class InvalidPropertyType extends Error {
	constructor(message?: string) {
		super(message ?? "The property type is invalid.");
		this.name = "InvalidPropertyType";
	}
}

export class InvalidAddonPath extends FileNotFoundError {
	constructor(message?: string) {
		super(message ?? "The addon path is invalid.");
		this.name = "InvalidAddonPath";
	}
}

export class NonChromeFingerprint extends Error {
	constructor(message?: string) {
		super(message ?? "A passed fingerprint is not a Chrome fingerprint.");
		this.name = "NonChromeFingerprint";
	}
}

export class InvalidOS extends Error {
	constructor(message?: string) {
		super(message ?? "The target OS is invalid.");
		this.name = "InvalidOS";
	}
}

export class GpuDisplayError extends VirtualDisplayError {
	constructor(message?: string) {
		super(message ?? "The headless='virtual-gpu' display cannot be used.");
		this.name = "GpuDisplayError";
	}
}

export class NoGpuFound extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "No GPU render node with a PCI address was found.");
		this.name = "NoGpuFound";
	}
}

export class AmbiguousGpuSelection extends GpuDisplayError {
	constructor(message?: string) {
		super(
			message ?? "Several GPUs match, or several exist and none was named.",
		);
		this.name = "AmbiguousGpuSelection";
	}
}

export class GpuNotFound extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "The GPU selector matches no GPU.");
		this.name = "GpuNotFound";
	}
}

export class CannotFindXorg extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "Xorg cannot be found.");
		this.name = "CannotFindXorg";
	}
}

export class CannotExecuteXorg extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "Xorg cannot be started, or exited right after starting.");
		this.name = "CannotExecuteXorg";
	}
}

export class GpuVtTakeover extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "The Xorg server switched the active console VT.");
		this.name = "GpuVtTakeover";
	}
}

export class GpuSoftwareFallback extends GpuDisplayError {
	constructor(message?: string) {
		super(message ?? "The browser on a GPU display never rendered on the GPU.");
		this.name = "GpuSoftwareFallback";
	}
}

export class CamouwolfNotInstalled extends FileNotFoundError {
	constructor(message?: string) {
		super(message ?? "Camouwolf is not installed.");
		this.name = "CamouwolfNotInstalled";
	}
}

/** Raised when Camouwolf's required runtime directory cannot be prepared. */
export class ProfileDirectoryError extends Error {
	constructor(message?: string, options?: ErrorOptions) {
		super(
			message ?? "Camouwolf's runtime directory could not be prepared.",
			options,
		);
		this.name = "ProfileDirectoryError";
	}
}

/** Python's ValueError (pycompat.ts re-exports it). */
export class ValueError extends Error {
	name = "ValueError";
}

/** Python's KeyError (pycompat.ts re-exports it). */
export class KeyError extends Error {
	name = "KeyError";
}

/** Python's NotImplementedError: an option Camouwolf refuses rather than ignores. */
export class NotImplementedError extends Error {
	name = "NotImplementedError";
}

/** Raised when `config` names a property the browser does not read. */
export class UnknownProperty extends KeyError {
	name = "UnknownProperty";
}

/**
 * Raised when the identity contradicts itself in a way a page can read, such
 * as a user agent and client hints naming different operating systems.
 */
export class IncoherentIdentity extends ValueError {
	name = "IncoherentIdentity";
}

/** Raised when the identity claims an OS this host cannot pass for. */
export class UnsupportedOSSpoof extends ValueError {
	name = "UnsupportedOSSpoof";
}
