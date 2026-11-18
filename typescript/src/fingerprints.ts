/**
 * Chrome identities: drawing one with fpgen and turning it into Camouwolf config.
 *
 * An fpgen fingerprint is one recorded Chrome on one real machine, so its
 * fields agree with each other; the launcher maps them and leaves them
 * together. What the record cannot know is filled in afterwards: the Chromium
 * milestone the binary really is, and the client hints that milestone and OS
 * imply (clienthints.ts).
 *
 * TypeScript twin of python/src/camouwolf/fingerprints.py.
 */
import { generate, InvalidConstraints } from "fpgen-js";
import * as random from "python-random";
import { type OsKey, retargetUserAgent, uaOs } from "./clienthints.js";
import {
	supported as cpuAffinitySupported,
	hostCoreCount,
} from "./cpu_affinity.js";
import { ValueError } from "./exceptions.js";
import { LAUNCHER_CONSTANTS, loadDataFile } from "./paths.js";
import { isPlainObject, num, pyStr, pyStrRepr, pyTruthy } from "./pycompat.js";
import { webglParameters } from "./webgl.js";

type Config = Record<string, any>;

/** The fpgen mapping file. */
export const FPGEN_DATA: Readonly<Record<string, any>> =
	loadDataFile<Record<string, any>>("fpgen.yml");

/** Camouwolf's OS names and the keys the rest of the launcher uses for them. */
export const OS_KEYS: Readonly<Record<string, OsKey>> = {
	macos: "mac",
	windows: "win",
	linux: "lin",
};
export const OS_NAMES: Readonly<Record<OsKey, string>> = {
	mac: "macos",
	win: "windows",
	lin: "linux",
};

/** The OS name ('macos', 'windows', 'linux') a navigator.platform belongs to. */
export function platformToOs(platform: string): string {
	if (platform.startsWith("Mac")) return "macos";
	if (platform.startsWith("Win")) return "windows";
	return "linux";
}

/** fpgen's OS names, from Camouwolf's. */
export const FPGEN_OS: Readonly<Record<string, string>> = {
	lin: "Linux",
	linux: "Linux",
	mac: "macOS",
	macos: "macOS",
	win: "Windows",
	windows: "Windows",
};

/** Python dict.get(key, default): the default only when the key is absent. */
function get(config: Config, key: string, dflt: any = undefined): any {
	return Object.hasOwn(config, key) ? config[key] : dflt;
}

function isNone(value: unknown): boolean {
	return value === null || value === undefined;
}

/** isinstance(value, int): a bool counts, an integral float does not reach here. */
function isInt(value: unknown): value is number | boolean {
	return (
		typeof value === "boolean" ||
		(typeof value === "number" && Number.isInteger(value))
	);
}

export interface ScreenBounds {
	minWidth?: number | null;
	maxWidth?: number | null;
	minHeight?: number | null;
	maxHeight?: number | null;
}

/**
 * A bound on the screen a generated fingerprint may claim: the generated
 * screen must fit inside the given fields.
 */
export class Screen {
	minWidth: number | null;
	maxWidth: number | null;
	minHeight: number | null;
	maxHeight: number | null;

	constructor({
		minWidth = null,
		maxWidth = null,
		minHeight = null,
		maxHeight = null,
	}: ScreenBounds = {}) {
		this.minWidth = minWidth;
		this.maxWidth = maxWidth;
		this.minHeight = minHeight;
		this.maxHeight = maxHeight;
	}

	/**
	 * The bound, as fpgen conditions. fpgen takes a predicate per field and
	 * rejects a value the predicate refuses, so a range is a closure rather
	 * than a list of allowed values.
	 */
	asConditions(): Record<string, (value: unknown) => boolean> {
		const conditions: Record<string, (value: unknown) => boolean> = {};
		const {
			minWidth: loW,
			maxWidth: hiW,
			minHeight: loH,
			maxHeight: hiH,
		} = this;
		const within = (v: unknown, lo: number | null, hi: number | null) =>
			isInt(v) &&
			(lo === null || Number(v) >= lo) &&
			(hi === null || Number(v) <= hi);
		if (loW !== null || hiW !== null)
			conditions["screen.width"] = (w) => within(w, loW, hiW);
		if (loH !== null || hiH !== null)
			conditions["screen.height"] = (h) => within(h, loH, hiH);
		return conditions;
	}

	/** dataclasses.asdict(), with the Python field names. */
	toPyDict(): Record<string, number | null> {
		return {
			min_width: this.minWidth,
			max_width: this.maxWidth,
			min_height: this.minHeight,
			max_height: this.maxHeight,
		};
	}
}

/** Logical CPUs this process may actually run on (cgroup/affinity aware). */
export function hostCpuCount(): number | null {
	return hostCoreCount() || null;
}

/**
 * Core counts real desktop machines ship with, from the recorded presets. 2 is
 * excluded although the scraped corpus has it: no Apple Silicon part has fewer
 * than 8 cores, so 2 beside the common "Apple M1" renderer is a machine that
 * does not exist. A host outside the table is snapped into it.
 */
export const PLAUSIBLE_CORE_COUNTS: readonly number[] =
	LAUNCHER_CONSTANTS.plausibleCoreCounts;

export interface HostCpu {
	/** Logical CPUs of the host (default: hostCpuCount()). */
	cpuCount?: number | null;
	/** Whether the host can pin processes to cores (default: cpu_affinity.supported()). */
	canPinHost?: boolean;
}

/**
 * navigator.hardwareConcurrency = the host's parallelism, snapped DOWN into
 * PLAUSIBLE_CORE_COUNTS -- or the fingerprint's own value (snapped, capped by
 * the host) when the browser can be pinned to that many cores.
 *
 * @param canPin false when nothing will pin the browser (launchServer, or
 *   launchOptions used directly); undefined/null: whatever the host supports.
 * @param host overrides for the host probes (tests).
 */
export function fixHardwareConcurrency(
	config: Config,
	canPin?: boolean | null,
	host: HostCpu = {},
): void {
	const n = "cpuCount" in host ? host.cpuCount : hostCpuCount();
	if (!n) return;
	const pinnable =
		(host.canPinHost ?? cpuAffinitySupported()) && canPin !== false;

	const cap = Math.trunc(n);
	const hostAllowed = PLAUSIBLE_CORE_COUNTS.filter((c) => c <= cap);
	const hostValue = hostAllowed.length
		? hostAllowed[hostAllowed.length - 1]
		: PLAUSIBLE_CORE_COUNTS[0];

	let drawn = config["navigator.hardwareConcurrency"];
	if (typeof drawn === "boolean") drawn = Number(drawn); // a Python bool is an int
	if (
		pinnable &&
		typeof drawn === "number" &&
		Number.isInteger(drawn) &&
		drawn >= 1
	) {
		const target = Math.min(drawn, cap);
		const allowed = PLAUSIBLE_CORE_COUNTS.filter((c) => c <= target);
		config["navigator.hardwareConcurrency"] = allowed.length
			? allowed[allowed.length - 1]
			: PLAUSIBLE_CORE_COUNTS[0];
		return;
	}
	config["navigator.hardwareConcurrency"] = hostValue;
}

/** Enforce inner <= outer <= avail <= screen on both axes. */
export function clampWindowDimensions(config: Config): void {
	for (const axis of ["Width", "Height"]) {
		const screen = config[`screen.${axis.toLowerCase()}`];
		const avail = config[`screen.avail${axis}`];
		const outer = config[`window.outer${axis}`];
		const inner = config[`window.inner${axis}`];

		// avail must not exceed screen
		if (pyTruthy(screen) && pyTruthy(avail) && num(avail) > num(screen)) {
			config[`screen.avail${axis}`] = screen;
		}
		const availClamped = get(config, `screen.avail${axis}`, screen);

		// outer must not exceed avail (or screen if avail is unknown)
		const outerCap = !isNone(availClamped) ? availClamped : screen;
		if (pyTruthy(outer) && pyTruthy(outerCap) && num(outer) > num(outerCap)) {
			const chrome = pyTruthy(inner) ? Math.max(0, num(outer) - num(inner)) : 0;
			config[`window.outer${axis}`] = outerCap;
			if (pyTruthy(inner))
				config[`window.inner${axis}`] = Math.max(1, num(outerCap) - chrome);
		}

		// inner must not exceed outer
		const outerClamped = get(config, `window.outer${axis}`, outer);
		const innerNow = config[`window.inner${axis}`];
		if (
			pyTruthy(innerNow) &&
			pyTruthy(outerClamped) &&
			num(innerNow) > num(outerClamped)
		) {
			config[`window.inner${axis}`] = outerClamped;
		}
	}
}

/**
 * Shrink screen.width/height down to the bounds of the real display, keeping
 * the taskbar delta. Run clampWindowDimensions afterwards.
 */
export function clampScreenToDisplay(
	config: Config,
	maxWidth: number | null | undefined,
	maxHeight: number | null | undefined,
): void {
	for (const [axis, cap] of [
		["width", maxWidth],
		["height", maxHeight],
	] as const) {
		const screen = config[`screen.${axis}`];
		if (!(pyTruthy(screen) && pyTruthy(cap)) || num(screen) <= (cap as number))
			continue;
		const availKey =
			axis === "width" ? "screen.availWidth" : "screen.availHeight";
		const avail = config[availKey];
		config[`screen.${axis}`] = cap;
		if (pyTruthy(avail)) {
			config[availKey] = Math.max(
				1,
				(cap as number) - Math.max(0, num(screen) - num(avail)),
			);
		}
	}
}

/** Keep the window box inside the screen: 0 <= screenX/Y <= screen - outer. */
export function clampWindowPosition(config: Config): void {
	for (const [axis, posKey] of [
		["Width", "window.screenX"],
		["Height", "window.screenY"],
	] as const) {
		const screen = config[`screen.${axis.toLowerCase()}`];
		const outer = config[`window.outer${axis}`];
		const pos = config[posKey];
		if (isNone(pos) || !(pyTruthy(screen) && pyTruthy(outer))) continue;
		config[posKey] = Math.max(0, Math.min(num(pos), num(screen) - num(outer)));
	}
}

/** The smallest screen mainstream hardware still ships. */
export const MODERN_SCREEN_FLOOR: readonly [number, number] = [1366, 768];

/**
 * Lift netbook-era screen geometry to the modern floor, keeping the
 * screen-to-avail gaps. Call BEFORE clampScreenToDisplay.
 */
export function raiseScreenToModernFloor(config: Config): void {
	const [minW, minH] = MODERN_SCREEN_FLOOR;
	const sw = config["screen.width"];
	const sh = config["screen.height"];
	if (!(pyTruthy(sw) && pyTruthy(sh)) || (num(sw) >= minW && num(sh) >= minH))
		return;

	// Measure the gaps before mutating, or they get folded into themselves.
	const aw = config["screen.availWidth"];
	const ah = config["screen.availHeight"];
	const gapW = pyTruthy(aw) ? num(sw) - num(aw) : null;
	const gapH = pyTruthy(ah) ? num(sh) - num(ah) : null;

	const newW = Math.max(num(sw), minW);
	const newH = Math.max(num(sh), minH);
	config["screen.width"] = newW;
	config["screen.height"] = newH;
	if (gapW !== null)
		config["screen.availWidth"] = Math.max(1, newW - Math.max(0, gapW));
	if (gapH !== null)
		config["screen.availHeight"] = Math.max(1, newH - Math.max(0, gapH));
}

const SOFTWARE_RENDERERS: readonly string[] = [
	"llvmpipe",
	"Microsoft Basic Render Driver",
	"SwiftShader",
	"Generic Renderer",
];

/**
 * Whether `renderer` is a software rasterizer rather than real hardware.
 * Case-insensitive: fpgen hands a condition's predicate the value lowercased.
 */
export function isSoftwareRenderer(
	renderer: string | null | undefined,
): boolean {
	if (!renderer) return false;
	const lowered = renderer.toLowerCase();
	return SOFTWARE_RENDERERS.some((name) =>
		lowered.includes(name.toLowerCase()),
	);
}

/**
 * The taskbar, menu bar or panel a desktop reserves, in CSS pixels. A screen
 * whose available area equals its whole area on both axes has none, which only
 * a kiosk shows.
 */
export const TASKBAR_HEIGHT: Readonly<Record<OsKey, number>> =
	LAUNCHER_CONSTANTS.taskbarHeight;

/** Reserve the OS's taskbar when the identity's available area is its whole screen. */
export function fixScreenNoTaskbar(config: Config, targetOs: OsKey): void {
	const width = config["screen.width"];
	const height = config["screen.height"];
	if (!(pyTruthy(width) && pyTruthy(height))) return;
	const availW = config["screen.availWidth"];
	const availH = config["screen.availHeight"];
	if (isNone(availW) || isNone(availH)) return;
	if (num(availW) !== num(width) || num(availH) !== num(height)) return;
	const taskbar = TASKBAR_HEIGHT[targetOs];
	config["screen.availHeight"] = num(height) - taskbar;
	if (targetOs === "mac") config["screen.availTop"] = taskbar;
	const outer = config["window.outerHeight"];
	if (pyTruthy(outer) && num(outer) > num(height) - taskbar) {
		config["window.outerHeight"] = num(height) - taskbar;
	}
}

/**
 * Families every installation of the OS has. fpgen records the fonts a probe
 * list found, so a short record (six families on some Linux machines) would
 * otherwise leave a page with almost nothing to render in.
 */
export const ESSENTIAL_FONTS: Readonly<Record<OsKey, string[]>> =
	LAUNCHER_CONSTANTS.essentialFonts;

/**
 * The devices a machine with nothing plugged in enumerates, before the page
 * has media permission: one entry per kind, with empty id, label and group.
 */
export const MEDIA_DEVICE_KINDS: readonly string[] = [
	"audioinput",
	"videoinput",
	"audiooutput",
];
export const DEFAULT_MEDIA_DEVICES: Readonly<Record<OsKey, string[]>> = {
	win: ["audioinput|||", "videoinput|||", "audiooutput|||"],
	mac: ["audioinput|||", "videoinput|||", "audiooutput|||"],
	lin: ["audioinput|||", "audiooutput|||"],
};

/**
 * fpgen's device counts as `mediaDevices:enumerated` entries.
 *
 * Before a page holds media permission Chrome lists at most one device of
 * each kind, with empty deviceId, label and groupId.
 */
export function mediaDevices(devices: unknown): string[] | null {
	if (!isPlainObject(devices)) return null;
	const listed = MEDIA_DEVICE_KINDS.filter(
		(kind) => num(pyTruthy(devices[kind]) ? devices[kind] : 0) > 0,
	).map((kind) => `${kind}|||`);
	return listed.length ? listed : null;
}

/** fpgen's speechSynthesis voices as `voices` entries: name|lang|uri|local|default. */
export function packVoices(voices: unknown): string[] | null {
	if (!Array.isArray(voices)) return null;
	const packed: string[] = [];
	for (const voice of voices) {
		if (
			!isPlainObject(voice) ||
			!pyTruthy(voice.name) ||
			!pyTruthy(voice.lang)
		) {
			continue;
		}
		const fields = [
			voice.name,
			voice.lang,
			pyTruthy(voice.voiceURI) ? voice.voiceURI : voice.name,
		].map(pyStr);
		// The separator; no recorded voice has one.
		if (fields.some((field) => field.includes("|"))) continue;
		packed.push(
			[
				...fields,
				pyTruthy(voice.localService) ? "1" : "0",
				pyTruthy(voice.default) ? "1" : "0",
			].join("|"),
		);
	}
	return packed.length ? packed : null;
}

/**
 * The WebGPU adapter info of an fpgen record, as webGpu:* keys.
 *
 * A record whose machine had no WebGPU adapter carries an empty list, and
 * nothing is claimed for it.
 */
export function webgpuAdapter(gpuInfo: unknown): Record<string, string> {
	const adapter = isPlainObject(gpuInfo) ? gpuInfo.adapter : null;
	const dflt = isPlainObject(adapter) ? adapter.default : null;
	const info = isPlainObject(dflt) ? dflt.info : null;
	if (!isPlainObject(info)) return {};
	const out: Record<string, string> = {};
	for (const key of ["vendor", "architecture", "device"]) {
		if (typeof info[key] === "string") out[`webGpu:${key}`] = info[key];
	}
	return out;
}

/** The identity's installed families: the record's, with the OS's own. */
export function fontList(fonts: unknown, targetOs: OsKey): string[] | null {
	if (!Array.isArray(fonts) || !fonts.length) return null;
	const families: string[] = fonts.filter(
		(f) => typeof f === "string" && f.length > 0,
	);
	const seen = new Set(families.map((f) => f.toLowerCase()));
	families.push(
		...ESSENTIAL_FONTS[targetOs].filter((f) => !seen.has(f.toLowerCase())),
	);
	return families;
}

/** Copy the scalar fields fpgen.yml names into Camouwolf config properties. */
function castToProperties(
	camouwolfData: Config,
	castEnum: Record<string, any>,
	fp: Record<string, any>,
): void {
	for (const [key, raw] of Object.entries(fp)) {
		const typeKey = Object.hasOwn(castEnum, key) ? castEnum[key] : undefined;
		if (!pyTruthy(typeKey)) continue;
		if (isPlainObject(raw)) {
			if (isPlainObject(typeKey)) castToProperties(camouwolfData, typeKey, raw);
			continue;
		}
		// fpgen writes a field the browser did not have as None or "undefined".
		if (isNone(raw) || raw === "" || raw === "undefined" || Array.isArray(raw))
			continue;
		if (typeof typeKey !== "string") continue;
		let data: any = raw;
		// Fix values that are out of bounds
		if (typeKey.startsWith("screen.") && isInt(data) && Number(data) < 0)
			data = 0;
		camouwolfData[typeKey] = data;
	}
}

/** Sets window.screenY based on the generated screenX value. */
export function handleScreenXY(
	camouwolfData: Config,
	fingerprint: Record<string, any>,
): void {
	// Skip if manually provided
	if ("window.screenY" in camouwolfData) return;
	const screen = pyTruthy(fingerprint.screen) ? fingerprint.screen : {};
	const window = pyTruthy(fingerprint.window) ? fingerprint.window : {};
	// Default screenX to 0 if not provided
	const screenX = window.screenX;
	if (!pyTruthy(screenX)) {
		camouwolfData["window.screenX"] = 0;
		camouwolfData["window.screenY"] = 0;
		return;
	}
	// If screenX is within [-50, 50], use the same value for screenY
	const x = num(screenX);
	if (Number.isInteger(x) && x >= -50 && x <= 50) {
		camouwolfData["window.screenY"] = screenX;
		return;
	}
	// The generator thinks the browser is windowed. Randomly generate a screenY.
	const screenY =
		num(pyTruthy(screen.availHeight) ? screen.availHeight : 0) -
		num(pyTruthy(window.outerHeight) ? window.outerHeight : 0);
	if (screenY === 0) camouwolfData["window.screenY"] = 0;
	else if (screenY > 0)
		camouwolfData["window.screenY"] = random.randrange(0, screenY);
	else camouwolfData["window.screenY"] = random.randrange(screenY, 0);
}

/** The OS key ('win', 'mac', 'lin') an fpgen fingerprint was recorded on. */
export function fingerprintOs(fingerprint: Record<string, any>): OsKey | null {
	const navigator = pyTruthy(fingerprint.navigator)
		? fingerprint.navigator
		: {};
	const userAgent = navigator.userAgent;
	return typeof userAgent === "string" ? uaOs(userAgent) : null;
}

/**
 * Converts an fpgen Chrome fingerprint to a Camouwolf config.
 *
 * `chromeVersion` is the Chromium milestone of the browser that will run; the
 * recorded user agent is rewritten to it.
 */
export function fromFpgen(
	fingerprint: Record<string, any>,
	chromeVersion?: string | number | null,
): Config {
	const camouwolfData: Config = {};
	castToProperties(camouwolfData, FPGEN_DATA, fingerprint);
	handleScreenXY(camouwolfData, fingerprint);

	const userAgent = camouwolfData["navigator.userAgent"];
	if (pyTruthy(chromeVersion) && typeof userAgent === "string") {
		camouwolfData["navigator.userAgent"] = retargetUserAgent(
			userAgent,
			pyStr(chromeVersion).split(".", 1)[0],
		);
	}
	const targetOs = fingerprintOs(fingerprint);

	const voices = packVoices(fingerprint.voices);
	if (voices) camouwolfData.voices = voices;
	const parameters = webglParameters(fingerprint.webgl, fingerprint.webgl2);
	if (
		Object.keys(parameters).length &&
		pyTruthy(camouwolfData["webGl:renderer"])
	) {
		camouwolfData["webGl:parameters"] = parameters;
	}
	Object.assign(camouwolfData, webgpuAdapter(fingerprint.gpuInfo));
	const devices = mediaDevices(fingerprint.devices);
	if (devices) camouwolfData["mediaDevices:enumerated"] = devices;
	if (targetOs) {
		const fonts = fontList(fingerprint.allFonts, targetOs);
		if (fonts) camouwolfData.fonts = fonts;
	}
	return camouwolfData;
}

/** Sets a custom outer window size and centers it in the screen (in place). */
export function handleWindowSize(
	fp: Record<string, any>,
	outerWidth: number,
	outerHeight: number,
): void {
	fp.screen ??= {};
	fp.window ??= {};
	const screen = fp.screen;
	const window = fp.window;

	// Center the window on the screen
	window.screenX =
		(window.screenX || 0) +
		Math.floor(((screen.width || outerWidth) - outerWidth) / 2);
	window.screenY = Math.floor(
		((screen.height || outerHeight) - outerHeight) / 2,
	);

	// Update inner dimensions if set
	if (pyTruthy(window.innerWidth)) {
		window.innerWidth = Math.max(
			outerWidth - (window.outerWidth || 0) + window.innerWidth,
			0,
		);
	}
	if (pyTruthy(window.innerHeight)) {
		window.innerHeight = Math.max(
			outerHeight - (window.outerHeight || 0) + window.innerHeight,
			0,
		);
	}

	// Set outer dimensions
	window.outerWidth = outerWidth;
	window.outerHeight = outerHeight;
}

export interface GenerateFingerprintOptions {
	/** Outer window size [width, height], applied after generation. */
	window?: readonly [number, number] | null;
	/** A bound on the generated screen (best-effort, see below). */
	screen?: Screen | null;
	/** Camouwolf OS name(s): 'linux', 'macos', 'windows' (or lin/mac/win). */
	os?: string | readonly string[] | null;
	/** Any other fpgen conditions. */
	[condition: string]: any;
}

/**
 * Generates a Chrome fingerprint with fpgen (the model must be installed:
 * `await ensureFpgenModel()`).
 *
 * `screen` bounds the generated screen; `window` overrides the outer window
 * size afterwards. `os` is Camouwolf's name for the platform ('linux',
 * 'macos', 'windows', or several); anything else is passed to fpgen as a
 * condition.
 */
export function generateFingerprint({
	window,
	screen,
	os,
	...conditions
}: GenerateFingerprintOptions = {}): Record<string, any> {
	if (os?.length) {
		const names = typeof os === "string" ? [os] : [...os];
		const resolved = names.map((n) => {
			const key = pyStr(n).toLowerCase();
			if (!Object.hasOwn(FPGEN_OS, key)) {
				throw new ValueError(
					`Unknown OS for fingerprint generation: ${pyStrRepr(key)}`,
				);
			}
			return FPGEN_OS[key];
		});
		// fpgen takes one value, or a set of alternatives (Python: a tuple; an
		// array is read as one value).
		conditions.os = resolved.length === 1 ? resolved[0] : new Set(resolved);
	}
	const screenConditions = screen ? screen.asConditions() : {};
	let fingerprint: Record<string, any>;
	try {
		fingerprint = generate({
			browser: "Chrome",
			...conditions,
			...screenConditions,
		});
	} catch (err) {
		if (
			!Object.keys(screenConditions).length ||
			!(err instanceof InvalidConstraints)
		)
			throw err;
		// The screen bound is best-effort: a display the model has nothing to
		// fit (a 1x1 Xvfb, a small panel) must not stop generation, and
		// clampScreenToDisplay() still bounds the result afterwards.
		fingerprint = generate({ browser: "Chrome", ...conditions });
	}
	if (window) handleWindowSize(fingerprint, window[0], window[1]);
	return fingerprint;
}
