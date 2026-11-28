/**
 * Shared scaffolding for the launch-layer tests: a scratch camouwolf cache
 * (XDG_CACHE_HOME, set before any src module is imported -- INSTALL_DIR is
 * computed at import time), the fixture browser bundle, and the fixed host
 * scripts/golden/launch_golden.py records the goldens on.
 *
 * Import this module FIRST, then import src modules dynamically.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const FIXTURES = path.join(HERE, "fixtures", "launch");
export const BUNDLE = path.join(FIXTURES, "bundle");
export const BUNDLE_EXE = path.join(BUNDLE, "camouwolf");

// Keep reading an fpgen model this machine already has: the scratch
// XDG_CACHE_HOME below would otherwise hide it and fetch another copy.
if (!process.env.CAMOUWOLF_FPGEN_DATA) {
	const xdg = process.env.XDG_CACHE_HOME?.trim();
	const base = xdg ? xdg : path.join(os.homedir(), ".cache");
	const own = path.join(base, "camouwolf", "fpgen");
	const shared = path.join(base, "fpgen");
	process.env.CAMOUWOLF_FPGEN_DATA =
		!fs.existsSync(own) && fs.existsSync(shared) ? shared : own;
}

export const SCRATCH = fs.mkdtempSync(
	path.join(os.tmpdir(), "camouwolf-launch-test-"),
);
process.env.XDG_CACHE_HOME = path.join(SCRATCH, "xdg-cache");
export const CACHE = path.join(SCRATCH, "xdg-cache", "camouwolf");
export const HOME = path.join(SCRATCH, "home");
fs.mkdirSync(HOME, { recursive: true });
for (const name of [
	"DISPLAY",
	"WAYLAND_DISPLAY",
	"CAMOUWOLF_EXECUTABLE_PATH",
	"CAMOUWOLF_GPU",
]) {
	delete process.env[name];
}

export const utils = await import("../src/utils.js");
export const fingerprints = await import("../src/fingerprints.js");
export const warnings = await import("../src/warnings.js");
export const addons = await import("../src/addons.js");
const { Geolocation, Locale } = await import("camoucore/locales.js");

/** Reassemble the chunked CAMOU_CONFIG_<n> env vars into the config. */
export function configOf(options: Record<string, any>): Record<string, any> {
	return JSON.parse(utils.camouConfigBlob(options));
}

const deps = utils.utilsDeps;
const ORIGINAL = { ...deps };
const ORIGINAL_ADDONS = { ...addons.addonsDeps };

/** Put every injection point back. */
export function restoreDeps(): void {
	Object.assign(deps, ORIGINAL);
	Object.assign(addons.addonsDeps, ORIGINAL_ADDONS);
}

/** The `host` object of tests/golden/launch.json. */
export interface GoldenHost {
	cpuCount: number;
	canPin: boolean;
	display: [number, number];
	publicIp: string;
	geolocation: {
		language: string;
		region: string;
		script: string;
		longitude: number;
		latitude: number;
		timezone: string;
		accuracy: number;
	};
	hostGpu: [string, string] | null;
	farblingToken: string;
}

export const DEFAULT_HOST: GoldenHost = {
	cpuCount: 16,
	canPin: true,
	display: [1920, 1080],
	publicIp: "203.0.113.7",
	geolocation: {
		language: "de",
		region: "DE",
		script: "Latn",
		longitude: 13.4105,
		latitude: 52.5244,
		timezone: "Europe/Berlin",
		accuracy: 25.5,
	},
	hostGpu: null,
	farblingToken: "00112233445566778899aabbccddeeff",
};

/**
 * The host every launch test runs against: what launchOptions() would read
 * from this machine, fixed. No display unless `hasDisplay`, no network, no
 * browser download, the fixture bundle as the installed browser.
 */
export function stubHost(
	osName: "lin" | "win" | "mac" = "lin",
	hasDisplay = false,
	host: GoldenHost = DEFAULT_HOST,
): void {
	deps.osName = () => osName;
	deps.installDir = () => CACHE;
	deps.environ = () => ({});
	addons.addonsDeps.addonsDir = path.join(CACHE, "addons");
	deps.warnIfPackageOutdated = () => undefined;
	deps.ensureBrowserProfileDir = () => undefined;
	deps.hostCpu = () => ({ cpuCount: host.cpuCount, canPinHost: host.canPin });
	deps.hasDisplay = () => hasDisplay;
	deps.largestDisplay = () => ({
		width: host.display[0],
		height: host.display[1],
	});
	deps.publicIp = async () => host.publicIp;
	deps.publicIpv6 = async () => null;
	deps.geoipAllowed = () => undefined;
	const geo = host.geolocation;
	deps.getGeolocation = async () =>
		new Geolocation(
			new Locale(geo.language, geo.region, geo.script),
			geo.longitude,
			geo.latitude,
			geo.timezone,
			geo.accuracy,
		);
	deps.hostGpu = async () => host.hostGpu;
	deps.farblingToken = () => host.farblingToken;
	deps.ensureCamouwolfInstalled = (async () => BUNDLE) as any;
	deps.installedVerStr = () => "152.0.7977.42-alpha.1";
	deps.launchPath = (() => BUNDLE_EXE) as any;
}

/** Run `fn` with warnings captured instead of printed. */
export async function quietly<T>(fn: () => Promise<T>): Promise<T> {
	const { result, error } = await warnings.recordWarnings(fn);
	if (error) throw error;
	return result as T;
}
