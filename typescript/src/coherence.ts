/**
 * Whole-identity coherence: the checks that look at more than one field.
 *
 * An identity is assembled from several sources: an fpgen record, the client
 * hints derived for the binary's milestone, the caller's own `config`, and the
 * host (its core count, its display). A combination no machine has can be built
 * from individually plausible parts: a Windows user agent beside a macOS
 * Sec-CH-UA-Platform, a Direct3D renderer on a Linux identity, an Apple GPU
 * behind an x86 Sec-CH-UA-Arch.
 *
 * Every identity passes through here on every launch, whatever it was built
 * from. Each rule states an invariant that holds on real Chrome, says how to
 * repair an identity that breaks it where a correct value is determined, and is
 * checked again afterwards. `validate()` reports what is still broken;
 * `apply()` repairs what it can.
 *
 * TypeScript twin of python/src/camouwolf/coherence.py.
 */
import * as clienthints from "./clienthints.js";
import { LAUNCHER_CONSTANTS } from "./paths.js";
import { isPyInt, num, pyRepr, pyRound, pyStr, pyTruthy } from "./pycompat.js";
import { backendMatchesOs } from "./webgl.js";

type Config = Record<string, any>;
type OsKey = clienthints.OsKey;

/**
 * screen.colorDepth: Chrome reports 24, or 30 on a deep-colour display (most
 * Macs). 32 appears in scraped data and is not a value Chrome emits.
 */
export const PLAUSIBLE_COLOR_DEPTH: ReadonlySet<number> = new Set([24, 30]);

/**
 * navigator.deviceMemory is bucketed: a power of two, floored at 0.25 and
 * capped (at 8 until Chrome 140, at 32 on desktop since).
 */
export const PLAUSIBLE_DEVICE_MEMORY: readonly number[] = [
	0.25, 0.5, 1, 2, 4, 8, 16, 32,
];

/**
 * navigator.hardwareConcurrency: at least one logical core, and no consumer
 * machine has more than this.
 */
export const MAX_PLAUSIBLE_CORES = 256;

/**
 * window.devicePixelRatio: the scaling steps each OS's display settings offer.
 * Scraped records also carry ratios such as 1.0909, which are a browser zoom
 * folded into the display's scale.
 */
export const PLAUSIBLE_DPR: Readonly<Record<string, readonly number[]>> =
	LAUNCHER_CONSTANTS.plausibleDpr;

/** The CSS geometry a ratio scales: device pixels / devicePixelRatio. */
export const CSS_GEOMETRY_KEYS: readonly string[] = [
	"screen.width",
	"screen.height",
	"screen.availWidth",
	"screen.availHeight",
	"screen.availLeft",
	"screen.availTop",
	"window.outerWidth",
	"window.outerHeight",
	"window.innerWidth",
	"window.innerHeight",
	"window.screenX",
	"window.screenY",
];

export interface Violation {
	rule: string;
	detail: string;
	/**
	 * The caller's own keys that the repair would have changed, and which were
	 * kept as passed instead. Empty when nothing of the caller's is involved.
	 */
	kept: string[];
}

export interface Rule {
	name: string;
	/** Returns a description of the breakage, or null when the identity holds. */
	check: (config: Config, targetOs: string) => string | null;
	/** Repairs the identity in place. null where no correct value is determined. */
	repair: ((config: Config, targetOs: string) => void) | null;
	/**
	 * Keys the repair only adjusts to stay consistent with what it repairs. A
	 * caller who set any of them keeps all of them as they were, the repair
	 * itself still runs, and they never block it.
	 */
	adjusts?: readonly string[];
}

function isNone(value: unknown): boolean {
	return value === null || value === undefined;
}

function renderer(config: Config): string {
	const r = config["webGl:renderer"];
	return pyTruthy(r) ? pyStr(r) : "";
}

function checkClientHints(config: Config, _targetOs: string): string | null {
	const problems = clienthints.assertCoherent(config);
	return problems.length ? problems.join(" ") : null;
}

function checkTargetOs(config: Config, targetOs: string): string | null {
	const platform = config["navigator.platform"];
	const os = targetOs as OsKey;
	if (pyTruthy(platform) && platform !== clienthints.NAV_PLATFORM[os]) {
		return (
			`navigator.platform is "${pyStr(platform)}" on a ${clienthints.CH_PLATFORM[os]} ` +
			`identity (expected "${clienthints.NAV_PLATFORM[os]}")`
		);
	}
	return null;
}

function checkGpuMatchesOs(config: Config, targetOs: string): string | null {
	const r = renderer(config);
	if (r && !backendMatchesOs(r, targetOs)) {
		return (
			`WebGL renderer "${r}" is not on an ANGLE backend Chrome uses on ` +
			`${clienthints.CH_PLATFORM[targetOs as OsKey]}`
		);
	}
	return null;
}

function appleGpu(config: Config): boolean {
	return renderer(config).includes("Apple M");
}

function checkMacArchitecture(config: Config, targetOs: string): string | null {
	const arch = config["userAgentData.architecture"];
	const r = renderer(config);
	if (targetOs !== "mac" || !pyTruthy(arch) || !r) return null;
	const expected = appleGpu(config) ? "arm" : "x86";
	if (arch !== expected) {
		return (
			`Sec-CH-UA-Arch is "${pyStr(arch)}" beside the WebGL renderer "${r}"; ` +
			`that GPU is in ${expected === "arm" ? "Apple Silicon" : "Intel"} Macs ("${expected}")`
		);
	}
	return null;
}

function repairMacArchitecture(config: Config, _targetOs: string): void {
	config["userAgentData.architecture"] = appleGpu(config) ? "arm" : "x86";
}

/** Python's `value in container` for numbers: 24.0 == 24, True == 1. */
function numberIn(value: unknown, container: Iterable<number>): boolean {
	if (
		typeof value !== "number" &&
		typeof value !== "boolean" &&
		typeof value !== "bigint" &&
		!(value !== null && typeof value === "object" && "value" in value)
	) {
		return false;
	}
	const n = Number(num(value));
	for (const item of container) if (item === n) return true;
	return false;
}

function checkColorDepth(config: Config, _targetOs: string): string | null {
	const depth = config["screen.colorDepth"];
	if (!isNone(depth) && !numberIn(depth, PLAUSIBLE_COLOR_DEPTH)) {
		return `screen.colorDepth ${pyStr(depth)} is not a value Chrome reports (24 or 30)`;
	}
	return null;
}

function repairColorDepth(config: Config, _targetOs: string): void {
	config["screen.colorDepth"] = 24;
}

function checkDeviceMemory(config: Config, _targetOs: string): string | null {
	const memory = config["navigator.deviceMemory"];
	if (!isNone(memory) && !numberIn(memory, PLAUSIBLE_DEVICE_MEMORY)) {
		return `navigator.deviceMemory ${pyStr(memory)} is not one of the buckets Chrome reports`;
	}
	return null;
}

function repairDeviceMemory(config: Config, _targetOs: string): void {
	const memory = num(config["navigator.deviceMemory"]);
	const below = PLAUSIBLE_DEVICE_MEMORY.filter((bucket) => bucket <= memory);
	// int(bucket) if bucket >= 1 else bucket: an integral bucket is an int, and
	// a plain number is one already.
	config["navigator.deviceMemory"] = below.length
		? below[below.length - 1]
		: PLAUSIBLE_DEVICE_MEMORY[0];
}

function checkCores(config: Config, _targetOs: string): string | null {
	const cores = config["navigator.hardwareConcurrency"];
	if (isNone(cores)) return null;
	if (
		!isPyInt(cores) ||
		typeof cores === "boolean" ||
		!(Number(cores) >= 1 && Number(cores) <= MAX_PLAUSIBLE_CORES)
	) {
		return `navigator.hardwareConcurrency ${pyRepr(cores)} is not a core count`;
	}
	return null;
}

function checkScreenShape(config: Config, _targetOs: string): string | null {
	const width = config["screen.width"];
	const height = config["screen.height"];
	if (!pyTruthy(width) || !pyTruthy(height)) return null;
	if (num(height) > num(width)) {
		return `portrait screen ${pyStr(width)}x${pyStr(height)}; desktop panels are landscape`;
	}
	if (num(width) < 1024) {
		return `screen ${pyStr(width)}x${pyStr(height)} is smaller than any current desktop panel`;
	}
	return null;
}

function checkAvailBounds(config: Config, _targetOs: string): string | null {
	const width = config["screen.width"];
	const height = config["screen.height"];
	const availW = config["screen.availWidth"];
	const availH = config["screen.availHeight"];
	if (pyTruthy(width) && pyTruthy(availW) && num(availW) > num(width)) {
		return `screen.availWidth ${pyStr(availW)} exceeds screen.width ${pyStr(width)}`;
	}
	if (pyTruthy(height) && pyTruthy(availH) && num(availH) > num(height)) {
		return `screen.availHeight ${pyStr(availH)} exceeds screen.height ${pyStr(height)}`;
	}
	return null;
}

function repairAvailBounds(config: Config, _targetOs: string): void {
	const width = config["screen.width"];
	const height = config["screen.height"];
	if (pyTruthy(width) && num(config["screen.availWidth"] ?? 0) > num(width)) {
		config["screen.availWidth"] = width;
	}
	if (
		pyTruthy(height) &&
		num(config["screen.availHeight"] ?? 0) > num(height)
	) {
		config["screen.availHeight"] = height;
	}
}

function checkWindowFits(config: Config, _targetOs: string): string | null {
	for (const axis of ["Width", "Height"]) {
		const screen = config[`screen.${axis.toLowerCase()}`];
		const outer = config[`window.outer${axis}`];
		if (pyTruthy(screen) && pyTruthy(outer) && num(outer) > num(screen)) {
			return `window.outer${axis} ${pyStr(outer)} exceeds screen.${axis.toLowerCase()} ${pyStr(screen)}`;
		}
	}
	return null;
}

function repairWindowFits(config: Config, _targetOs: string): void {
	for (const axis of ["Width", "Height"]) {
		const screen = config[`screen.${axis.toLowerCase()}`];
		const outer = config[`window.outer${axis}`];
		if (pyTruthy(screen) && pyTruthy(outer) && num(outer) > num(screen)) {
			const avail = config[`screen.avail${axis}`];
			config[`window.outer${axis}`] = pyTruthy(avail) ? avail : screen;
		}
	}
}

/** A devicePixelRatio no display mode of the OS offers. */
export function isZoomedRatio(dpr: unknown, targetOs: string): boolean {
	const allowed = PLAUSIBLE_DPR[targetOs];
	if (!allowed || typeof dpr !== "number" || !(dpr > 0)) return false;
	return !allowed.includes(dpr);
}

function checkDevicePixelRatio(
	config: Config,
	targetOs: string,
): string | null {
	const dpr = config["window.devicePixelRatio"];
	if (!isNone(dpr) && isZoomedRatio(dpr, targetOs))
		return `window.devicePixelRatio ${pyRepr(dpr)} is not a display mode ${targetOs} offers`;
	return null;
}

/**
 * Move the ratio to the nearest display step and rescale the CSS geometry by
 * the zoom that was folded in, so the screen is still the display behind it:
 * 1760x990 at 1.0909 is a 1920x1080 monitor at 1.
 */
/** `keep` names sizes that are not the record's (a caller's window) and stay
 * as they are. */
export function repairDevicePixelRatio(
	config: Config,
	targetOs: string,
	keep: readonly string[] = [],
): void {
	const dpr = config["window.devicePixelRatio"];
	if (!isZoomedRatio(dpr, targetOs)) return;
	const step = PLAUSIBLE_DPR[targetOs].reduce((best, v) =>
		Math.abs(v - dpr) < Math.abs(best - dpr) ? v : best,
	);
	config["window.devicePixelRatio"] = step;
	const scale = dpr / step;
	for (const key of CSS_GEOMETRY_KEYS) {
		const value = config[key];
		if (!keep.includes(key) && typeof value === "number")
			config[key] = pyRound(value * scale);
	}
}

export const RULES: readonly Rule[] = [
	{ name: "client-hints", check: checkClientHints, repair: null },
	{ name: "target-os", check: checkTargetOs, repair: null },
	{ name: "gpu-matches-os", check: checkGpuMatchesOs, repair: null },
	{
		name: "mac-architecture",
		check: checkMacArchitecture,
		repair: repairMacArchitecture,
	},
	{ name: "color-depth", check: checkColorDepth, repair: repairColorDepth },
	{
		name: "device-memory",
		check: checkDeviceMemory,
		repair: repairDeviceMemory,
	},
	{ name: "hardware-concurrency", check: checkCores, repair: null },
	{
		name: "device-pixel-ratio",
		check: checkDevicePixelRatio,
		repair: repairDevicePixelRatio,
		adjusts: CSS_GEOMETRY_KEYS,
	},
	{ name: "screen-shape", check: checkScreenShape, repair: null },
	{ name: "avail-bounds", check: checkAvailBounds, repair: repairAvailBounds },
	{
		name: "window-fits-screen",
		check: checkWindowFits,
		repair: repairWindowFits,
	},
];

/**
 * The rules whose violation is refused at launch rather than warned about: a
 * page, or the server before any page runs, reads each of them directly.
 */
export const FATAL_RULES: ReadonlySet<string> = new Set([
	"client-hints",
	"target-os",
	"gpu-matches-os",
]);

/** Whether the screen is one no desktop reports (portrait, or tiny). */
export function screenIsImplausible(config: Config): boolean {
	return checkScreenShape(config, "") !== null;
}

/**
 * Turn a portrait screen landscape, keeping the panel's own dimensions.
 *
 * A recorded 1440x2560 screen is a phone panel, or a desktop one captured
 * while rotated. Either way a desktop identity that claims it is answering a
 * `matchMedia('(orientation: portrait)')` the way no desktop does, so the axes
 * are swapped rather than the numbers invented. Run before the window clamps,
 * which then bound the window to the new screen.
 */
export function repairScreenOrientation(config: Config): boolean {
	const width = config["screen.width"];
	const height = config["screen.height"];
	if (!pyTruthy(width) || !pyTruthy(height) || num(height) <= num(width))
		return false;
	config["screen.width"] = height;
	config["screen.height"] = width;
	const availW = config["screen.availWidth"];
	const availH = config["screen.availHeight"];
	if (pyTruthy(availW) && pyTruthy(availH)) {
		config["screen.availWidth"] = availH;
		config["screen.availHeight"] = availW;
	}
	return true;
}

function changed(
	before: Config,
	after: Config,
	keys: ReadonlySet<string>,
): string[] {
	return [...keys]
		.filter(
			(k) =>
				k in before !== k in after ||
				JSON.stringify(before[k]) !== JSON.stringify(after[k]),
		)
		.sort();
}

/** Every invariant this identity breaks. Empty means coherent. */
export function validate(config: Config, targetOs: string): Violation[] {
	const violations: Violation[] = [];
	for (const rule of RULES) {
		const detail = rule.check(config, targetOs);
		if (detail) violations.push({ rule: rule.name, detail, kept: [] });
	}
	return violations;
}

/**
 * Repair what is determined, and report what is left.
 *
 * A rule with no repair -- a Windows GPU on a macOS identity, a portrait
 * screen -- cannot be corrected without inventing a machine, so it is returned
 * for the caller to decide about. The caller that draws the identity can
 * simply draw again.
 *
 * `keep` names the keys the caller set. A repair that would change one of
 * them is not made: the caller's value stands, and the violation comes back
 * with those keys in `kept` so the launcher can warn about it. A repair that
 * only touches other keys still runs.
 */
export function apply(
	config: Config,
	targetOs: string,
	keep: ReadonlySet<string> = new Set(),
): Violation[] {
	const kept = new Map<string, string[]>();
	for (const rule of RULES) {
		if (!rule.repair || !rule.check(config, targetOs)) continue;
		const repaired: Config = { ...config };
		rule.repair(repaired, targetOs);
		if (rule.adjusts?.some((key) => keep.has(key))) {
			for (const key of rule.adjusts) {
				if (key in config) repaired[key] = config[key];
				else delete repaired[key];
			}
		}
		const blocked = changed(config, repaired, keep);
		if (blocked.length) {
			kept.set(rule.name, blocked);
		} else {
			for (const key of Object.keys(config))
				if (!(key in repaired)) delete config[key];
			Object.assign(config, repaired);
		}
	}
	return validate(config, targetOs).map((v) => ({
		...v,
		kept: kept.get(v.rule) ?? [],
	}));
}
