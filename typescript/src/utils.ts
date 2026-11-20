/**
 * Launch-option assembly, the twin of python/src/camouwolf/utils.py:
 * launchOptions() must return what launch_options() does
 * (tests/launch-golden.test.ts). `utilsDeps` holds what the Python tests
 * monkeypatch.
 */
import { spawnSync } from "node:child_process";
import { createHash, randomInt } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { inspect } from "node:util";
import * as cpuAffinity from "camoucore/cpu_affinity.js";
import { hasDisplay, largestDisplay } from "camoucore/display.js";
import { geoipAllowed, getGeolocation } from "camoucore/geolocation.js";
import {
	type ProxyConfig,
	ProxyHelper,
	publicIP,
	publicIPv6,
	validIPv4,
} from "camoucore/ip.js";
import { handleLocales } from "camoucore/locales.js";
import { VirtualDisplay } from "camoucore/virtdisplay.js";
import { ensureModel } from "fpgen-js";
import { addDefaultAddons, confirmPaths, type DefaultAddon } from "./addons.js";
import * as clienthints from "./clienthints.js";
import * as coherence from "./coherence.js";
import { attachDriverDefaults } from "./driver_defaults.js";
import {
	IncoherentIdentity,
	InvalidOS,
	InvalidPropertyType,
	NonChromeFingerprint,
	NotImplementedError,
	UnknownIPLocation,
	UnknownProperty,
	UnsupportedOSSpoof,
	ValueError,
} from "./exceptions.js";
import {
	clampScreenToDisplay,
	clampWindowDimensions,
	clampWindowPosition,
	DEFAULT_MEDIA_DEVICES,
	ESSENTIAL_FONTS,
	fingerprintOs,
	fixHardwareConcurrency,
	fixScreenNoTaskbar,
	fromFpgen,
	generateFingerprint,
	type HostCpu,
	handleWindowSize,
	OS_KEYS,
	OS_NAMES,
	raiseScreenToModernFloor,
	Screen,
	snapWindowToDevicePixels,
	windowSwitchBounds,
} from "./fingerprints.js";
import { GPU_SWITCHES, GpuDisplay, proveGpu, selectGpu } from "./gpudisplay.js";
import { type HumanizeSetting, normalize } from "./humanize.js";
import { INSTALL_DIR, loadDataFile, OS_NAME } from "./paths.js";
import {
	buildFile,
	ensureBrowserProfileDir,
	ensureCamouwolfInstalled,
	installedVerStr,
	launchPath,
	selectedLaunchPath,
	warnIfPackageOutdated,
} from "./pkgman.js";
import { PRODUCT } from "./product.js";
import {
	comparePyStr,
	isPlainObject,
	num,
	orjsonDumps,
	PyFloat,
	pyRepr,
	pyStr,
	pyStrRepr,
	pyTruthy,
	pyTypeName,
} from "./pycompat.js";
import {
	auditSwitches,
	headlessPointerSettings,
	IGNORED_DEFAULT_ARGS,
	LAUNCHER_DISABLED_FEATURES,
	mergeListSwitches,
	PLAYWRIGHT_DISABLED_FEATURES,
	PLAYWRIGHT_HEADLESS_BLINK_SETTINGS,
} from "./switches.js";
import { LeakWarning, loadWarnings, warn } from "./warnings.js";
import { chromeGpus, usableGpu } from "./webgl.js";

type ListOrString = string | readonly string[];
type Config = Record<string, any>;
export type TargetOS = clienthints.OsKey;
export type OsName = "windows" | "macos" | "linux";
export type EnvVars = Record<string, string | number | boolean | undefined>;

/** System colours of each OS's default theme, by OS name. */
export const SYSTEM_COLORS: Readonly<Record<string, Record<string, number>>> =
	loadDataFile<Record<string, Record<string, number>>>(
		"data/system-colors.json",
	);

/**
 * What this host can pass for. A Linux build can render as Windows (the
 * bundled Windows fonts, the UCRT math replica, patches 0019 and 0022);
 * nothing makes a Linux or Windows host render as macOS, or a Windows or macOS
 * host as Linux.
 */
export const ALLOWED_OS_TARGETS: Readonly<Record<TargetOS, readonly OsName[]>> =
	{
		win: ["windows"],
		lin: ["linux", "windows"],
		mac: ["macos"],
	};

/**
 * How many fingerprints to draw before giving up on one whose GPU the identity
 * can claim. fpgen's GPU condition already excludes software renderers, so a
 * redraw only happens for a record whose ANGLE backend its own OS never uses.
 */
export const MAX_FINGERPRINT_DRAWS = 20;

export const FONT_FILE_SUFFIXES: readonly string[] = [
	".ttf",
	".otf",
	".ttc",
	".woff",
	".woff2",
	".pfb",
];

/** What the launch reads from the host; the tests replace these. */
export const utilsDeps = {
	osName: (): TargetOS => OS_NAME,
	installDir: (): string => INSTALL_DIR,
	/** os.environ, for CAMOUWOLF_EXECUTABLE_PATH and the default `env`. */
	environ: (): NodeJS.ProcessEnv => process.env,
	hasDisplay,
	largestDisplay,
	/** Overrides for the host CPU probes of fixHardwareConcurrency. */
	hostCpu: (): HostCpu => ({}),
	print: (line: string): void => {
		process.stdout.write(`${line}\n`);
	},
	ensureBrowserProfileDir,
	ensureCamouwolfInstalled,
	warnIfPackageOutdated,
	addDefaultAddons,
	confirmPaths,
	installedVerStr: (): string => installedVerStr(),
	launchPath,
	selectedLaunchPath,
	/** fpgen's model is fetched on first use, like Python's. */
	ensureModel: async (): Promise<void> => {
		await ensureModel();
	},
	generateFingerprint,
	chromeGpus,
	publicIp: publicIP,
	publicIpv6: publicIPv6,
	geoipAllowed,
	getGeolocation,
	selectGpu,
	/** secrets.randbelow(2**32 - 1) + 1 */
	canvasSeed: (): number => randomInt(0, 2 ** 32 - 1) + 1,
};

/*
 * Environment: the identity travels in CAMOU_CONFIG_<n>, never in argv.
 */

/**
 * orjson.dumps(config): compact, UTF-8, insertion order. JavaScript has one
 * number type, so the Python int/float split is carried the way pycompat
 * carries it: a safe-integer number is an int, a bigint is an int beyond
 * 2**53, a PyFloat is an integral float, and any other number is a float.
 */
export function configJson(value: unknown): string {
	return orjsonDumps(value, false);
}

/** What orjson.dumps refuses, as its error text; null when serializable. */
function unserializable(value: unknown): string | null {
	if (value === undefined) return "Type is not JSON serializable: undefined";
	if (value === null) return null;
	if (typeof value === "function" || typeof value === "symbol")
		return `Type is not JSON serializable: ${typeof value}`;
	if (typeof value === "number" && !Number.isFinite(value)) return null;
	if (typeof value !== "object" || value instanceof PyFloat) return null;
	if (Array.isArray(value)) {
		for (const item of value) {
			const problem = unserializable(item);
			if (problem) return problem;
		}
		return null;
	}
	if (!isPlainObject(value)) {
		return `Type is not JSON serializable: ${value.constructor?.name ?? "object"}`;
	}
	for (const item of Object.values(value)) {
		const problem = unserializable(item);
		if (problem) return problem;
	}
	return null;
}

/** Slice `s` into chunks of `size` code points (Python str slicing). */
function chunkCodePoints(s: string, size: number): string[] {
	// Fast path: no astral characters, so UTF-16 units == code points.
	if (!/[\ud800-\udfff]/.test(s)) {
		const out: string[] = [];
		for (let i = 0; i < s.length; i += size) out.push(s.slice(i, i + size));
		return out;
	}
	const cps = Array.from(s);
	const out: string[] = [];
	for (let i = 0; i < cps.length; i += size) {
		out.push(cps.slice(i, i + size).join(""));
	}
	return out;
}

function isDir(p: string): boolean {
	try {
		return fs.statSync(p).isDirectory();
	} catch {
		return false;
	}
}

/**
 * The bundled font directories an identity on `osDir` may see.
 *
 * fontconfig scans a <dir> recursively, so the parent is never named when the
 * bundle is split by OS: it would make every other OS's files candidates for
 * glyph fallback.
 */
function fontScanDirs(fontsDir: string, osDir: string): string[] {
	const groupsPath = path.join(fontsDir, "groups.json");
	const osKey = Object.hasOwn(OS_KEYS, osDir) ? OS_KEYS[osDir] : undefined;
	if (osKey && fs.existsSync(groupsPath)) {
		let readBy: unknown[] = [];
		try {
			const parsed = JSON.parse(fs.readFileSync(groupsPath, "utf8"));
			readBy = parsed?.readBy?.[osKey] ?? [];
		} catch {
			readBy = [];
		}
		const dirs = readBy
			.map((g) => path.join(fontsDir, String(g)))
			.filter((d) => isDir(d));
		if (dirs.length) return dirs;
	}
	if (isDir(path.join(fontsDir, osDir))) return [path.join(fontsDir, osDir)];
	return [fontsDir];
}

/** os.walk(): symlinked directories are listed, not entered. */
function hasFonts(directories: readonly string[]): boolean {
	const walk = (directory: string): boolean => {
		let entries: fs.Dirent[];
		try {
			entries = fs.readdirSync(directory, { withFileTypes: true });
		} catch {
			return false;
		}
		const subdirs: string[] = [];
		for (const entry of entries) {
			const full = path.join(directory, entry.name);
			if (entry.isDirectory()) {
				subdirs.push(full);
				continue;
			}
			if (entry.isSymbolicLink() && isDir(full)) continue;
			const lowered = entry.name.toLowerCase();
			if (FONT_FILE_SUFFIXES.some((suffix) => lowered.endsWith(suffix)))
				return true;
		}
		return subdirs.some(walk);
	};
	return directories.some(walk);
}

/**
 * A runtime fontconfig for an identity on `osDir` ('linux', 'macos',
 * 'windows'), from the bundle beside the binary; null when the bundle ships no
 * fonts for that OS.
 *
 * The bundled fonts.conf names its font directory relative to the working
 * directory, which breaks when the launcher runs anywhere else, so a copy with
 * absolute paths is written to the cache directory (not into the bundle, which
 * is commonly read-only at launch). Loaded through FONTCONFIG_FILE it is the
 * only config: the host's /etc/fonts rules, and so the host's fonts, are not
 * read.
 */
export function generateFontconfig(
	executablePath: string,
	osDir: string,
): string | null {
	const fontsConf = buildFile(
		executablePath,
		path.join("fontconfig", osDir, "fonts.conf"),
	);
	const fontsDir = buildFile(executablePath, "fonts");
	if (!fs.existsSync(fontsConf)) return null;
	const scanDirs = fontScanDirs(fontsDir, osDir);
	// A bundle whose fonts were not restored still ships fonts.conf. Loading it
	// would leave the browser with no fonts at all.
	if (!hasFonts(scanDirs)) return null;

	const content = fs
		.readFileSync(fontsConf, "utf8")
		.replaceAll('<dir prefix="cwd">fonts</dir>', () =>
			scanDirs.map((d) => `<dir>${d}</dir>`).join("\n\t"),
		);
	const cacheDir = path.join(utilsDeps.installDir(), "fontconfig");
	fs.mkdirSync(cacheDir, { recursive: true });
	const digest = createHash("sha256")
		.update(content)
		.digest("hex")
		.slice(0, 12);
	const runtimeConf = path.join(cacheDir, `fonts-${digest}.conf`);
	if (!fs.existsSync(runtimeConf)) fs.writeFileSync(runtimeConf, content);
	return runtimeConf;
}

/**
 * The config as CAMOU_CONFIG_1..N environment variables.
 *
 * Windows limits one variable to 32767 characters and, through some launch
 * paths, to 2047, so the JSON is split there; elsewhere at 32767.
 */
export function configEnvVars(
	configMap: Config,
	chunkSize?: number | null,
): Record<string, string> {
	const problem = unserializable(configMap);
	if (problem) {
		throw new InvalidPropertyType(
			`The config cannot be serialized to JSON: ${problem}`,
		);
	}
	const configStr = configJson(configMap);
	const size = chunkSize ?? (utilsDeps.osName() === "win" ? 2047 : 32767);
	const out: Record<string, string> = {};
	chunkCodePoints(configStr, size).forEach((chunk, i) => {
		out[`CAMOU_CONFIG_${i + 1}`] = chunk;
	});
	return out;
}

/**
 * Gets the environment variables for Camouwolf: the config, and what the
 * identity's OS needs from the process environment.
 *
 * `executablePath` is the executable about to launch; its bundle's fontconfig
 * is used.
 */
export function getEnvVars(
	configMap: Config,
	userAgentOs: TargetOS,
	executablePath?: string | null,
): Record<string, string> {
	const envVars: Record<string, string> = configEnvVars(configMap);
	const osName = utilsDeps.osName();

	// Windows resolves the sin/cos/atan2 behind DOMMatrix.rotate and canvas
	// ctx.rotate to ucrtbase; every other host resolves them to its own libm,
	// which disagrees by 1 ULP. The browser carries a bit-exact replica behind
	// this variable (browser/patches/0018-ucrt-math.patch). An environment
	// variable because every process needs the answer, before any config exists.
	if (userAgentOs === "win" && osName !== "win") {
		envVars.CAMOUWOLF_UCRT_MATH = "1";
	}

	// The browser installs `timezone` as its renderers' host zone (patch 0006).
	// TZ says the same to every other process of the browser, and to ICU before
	// any config is parsed, so no process can answer with the host's zone.
	// Windows does not read TZ.
	const timezone = configMap.timezone;
	if (pyTruthy(timezone) && osName !== "win") envVars.TZ = timezone;

	if (osName === "lin" && executablePath) {
		const fontconfig = generateFontconfig(
			executablePath,
			OS_NAMES[userAgentOs],
		);
		if (fontconfig) envVars.FONTCONFIG_FILE = fontconfig;
	}
	return envVars;
}

/*
 * Config validation
 */

interface PropertyEntry {
	property: string;
	type: string;
	/** "brand" for a UA-CH brand list; an array holds strings otherwise. */
	items?: string;
}

/**
 * Loads the config schema: property -> its entry. A build that ships its own
 * properties.json beside the binary is validated against that one; otherwise
 * the schema packaged with this library is used.
 */
function loadProperties(
	executablePath?: string | null,
): Record<string, PropertyEntry> {
	let propList: PropertyEntry[] | undefined;
	if (executablePath) {
		const shipped = buildFile(executablePath, "properties.json");
		if (fs.existsSync(shipped))
			propList = JSON.parse(fs.readFileSync(shipped, "utf8"));
	}
	propList ??= loadDataFile<PropertyEntry[]>("data/properties.json");
	const out: Record<string, PropertyEntry> = {};
	for (const prop of propList) out[prop.property] = prop;
	return out;
}

/**
 * Validates the config map: every key must be one the browser reads, with a
 * value of its type. An unknown key throws UnknownProperty.
 */
export function validateConfig(
	configMap: Config,
	executablePath?: string | null,
): void {
	const properties = loadProperties(executablePath);

	for (const [key, value] of Object.entries(configMap)) {
		const entry = Object.hasOwn(properties, key) ? properties[key] : undefined;
		if (!entry) {
			throw new UnknownProperty(
				`Unknown property '${key}': this Camouwolf build reads no such config key. ` +
					`Known properties: ${Object.keys(properties).sort(comparePyStr).join(", ")}`,
			);
		}
		const expectedType = entry.type;
		if (!validateType(value, expectedType)) {
			throw new InvalidPropertyType(
				`Invalid type for property ${key}. Expected ${expectedType}, got ${pyTypeName(value)}`,
			);
		}
		if (expectedType === "array" && entry.items === "brand") {
			// A UA-CH brand list: [{"brand": ..., "version": ...}, ...], in order.
			const isBrand = (item: unknown): boolean => {
				if (!isPlainObject(item)) return false;
				const keys = Object.keys(item);
				return (
					keys.length === 2 &&
					"brand" in item &&
					"version" in item &&
					Object.values(item).every((v) => typeof v === "string")
				);
			};
			if (!(value as unknown[]).every(isBrand)) {
				throw new InvalidPropertyType(
					`Invalid type for property ${key}. Expected an array of {"brand", "version"} objects.`,
				);
			}
		} else if (
			expectedType === "array" &&
			!(value as unknown[]).every((item) => typeof item === "string")
		) {
			throw new InvalidPropertyType(
				`Invalid type for property ${key}. Expected an array of strings.`,
			);
		}
		if (key === "webGl:parameters") validateWebglParameters(value);
		if (key === "systemColors") {
			const allInts = Object.values(value).every(
				(v) =>
					typeof v === "bigint" ||
					(typeof v === "number" && Number.isInteger(v)),
			);
			if (!allInts) {
				throw new InvalidPropertyType(
					"Invalid systemColors: every value must be an 0xRRGGBB integer.",
				);
			}
		}
	}
}

/**
 * Every webGl:parameters entry is a decimal pname and a scalar; the browser
 * reads nothing else (arrays included).
 */
export function validateWebglParameters(parameters: Config): void {
	for (const [pname, value] of Object.entries(parameters)) {
		if (!/^\p{Nd}+$/u.test(pname)) {
			throw new InvalidPropertyType(
				`Invalid webGl:parameters key ${pyStrRepr(pname)}: expected a decimal GLenum.`,
			);
		}
		const scalar =
			typeof value === "boolean" ||
			typeof value === "number" ||
			typeof value === "bigint" ||
			typeof value === "string" ||
			value instanceof PyFloat;
		if (!scalar) {
			throw new InvalidPropertyType(
				`Invalid webGl:parameters[${pname}]: expected a number, boolean or string, ` +
					`got ${pyTypeName(value)}.`,
			);
		}
	}
}

/** Validates the type of the value. */
export function validateType(value: unknown, expectedType: string): boolean {
	if (expectedType === "bool") return typeof value === "boolean";
	if (typeof value === "boolean") return false;
	const isNumber =
		typeof value === "number" ||
		typeof value === "bigint" ||
		value instanceof PyFloat;
	// isinstance(value, int) or (isinstance(value, float) and value.is_integer())
	const integral =
		typeof value === "bigint" || (isNumber && Number.isInteger(num(value)));
	switch (expectedType) {
		case "str":
			return typeof value === "string";
		case "int":
			return integral;
		case "uint":
			return integral && Number(num(value)) >= 0;
		case "double":
			return isNumber;
		case "array":
			return Array.isArray(value);
		case "dict":
			return isPlainObject(value);
		default:
			return false;
	}
}

/*
 * Identity helpers
 */

/**
 * Gets the OS from the config if the user agent is set, otherwise returns the
 * OS of the current system.
 */
export function getTargetOs(config: Config): TargetOS {
	const userAgent = config["navigator.userAgent"];
	if (pyTruthy(userAgent)) {
		const target = clienthints.uaOs(pyStr(userAgent));
		if (!target) {
			throw new ValueError("Could not determine OS from user agent");
		}
		return target;
	}
	return utilsDeps.osName();
}

/** Checks if the target OS is valid. */
export function checkValidOs(osValue: ListOrString): void {
	if (typeof osValue !== "string") {
		for (const osName of osValue) checkValidOs(osName);
		return;
	}
	// Assert that the OS is lowercase (str.islower(): has a cased char, none upper)
	if (
		!(osValue !== osValue.toUpperCase() && osValue === osValue.toLowerCase())
	) {
		throw new InvalidOS(`OS values must be lowercase: '${osValue}'`);
	}
	// Assert that the OS is supported by Camouwolf
	if (!["windows", "macos", "linux"].includes(osValue)) {
		throw new InvalidOS(`Camouwolf does not support the OS: '${osValue}'`);
	}
}

/** Refuse an identity on an OS this host cannot pass for. */
export function checkOsPolicy(
	names: readonly string[],
	allowOsMismatch?: boolean | null,
): void {
	if (allowOsMismatch) return;
	const host = utilsDeps.osName();
	const allowed: readonly string[] = ALLOWED_OS_TARGETS[host];
	const bad = names.filter((name) => !allowed.includes(name));
	if (bad.length) {
		throw new UnsupportedOSSpoof(
			`Camouwolf cannot pass a ${OS_NAMES[host]} host off as: ${bad.join(", ")}. ` +
				"Supported host -> identity pairs: windows -> windows, linux -> linux, " +
				"linux -> windows, macos -> macos. Pass allow_os_mismatch=True to override.",
		);
	}
}

/**
 * Merges new keys/values from the source into the target, given that the key
 * does not exist in the target.
 */
export function mergeInto(target: Config, source: Config): void {
	for (const [key, value] of Object.entries(source)) {
		if (!(key in target)) target[key] = value;
	}
}

/** dict.setdefault() */
function setDefault(target: Config, key: string, value: unknown): void {
	if (!(key in target)) target[key] = value;
}

/** Checks if a domain is set in the config. */
export function isDomainSet(config: Config, ...properties: string[]): boolean {
	for (const prop of properties) {
		// A trailing . or : makes it a prefix
		if (prop.endsWith(".") || prop.endsWith(":")) {
			if (Object.keys(config).some((key) => key.startsWith(prop))) return true;
		} else if (prop in config) {
			return true;
		}
	}
	return false;
}

/**
 * Warns for each coherence violation the launch left in place because the
 * caller set the values involved. Camouwolf does not overwrite what the caller
 * passed, but an identity no real machine presents is detectable.
 */
export function warnKeptIncoherent(violations: coherence.Violation[]): void {
	for (const violation of violations) {
		if (!violation.kept.length) continue;
		const keys = violation.kept.join(", ");
		warn(
			loadWarnings()
				.caller_value_incoherent.replaceAll("{keys}", () => keys)
				.replaceAll("{detail}", () => violation.detail),
			"LeakWarning",
		);
	}
}

/**
 * Warns the user if they are manually setting properties that Camouwolf
 * already sets internally.
 */
export function warnManualConfig(config: Config): void {
	if (isDomainSet(config, "navigator.languages"))
		LeakWarning.warn(loadWarnings().locale, false);
	if (isDomainSet(config, "geolocation:", "timezone"))
		LeakWarning.warn(loadWarnings().geolocation, false);
	if (
		Object.keys(config).some(
			(key) =>
				(key.startsWith("navigator.") || key.startsWith("userAgentData.")) &&
				key !== "navigator.languages",
		)
	) {
		LeakWarning.warn(loadWarnings().navigator, false);
	}
	if (isDomainSet(config, "screen.", "window."))
		LeakWarning.warn(loadWarnings().viewport, false);
}

/**
 * Asserts that the passed fingerprint is a Chrome fingerprint, and warns the
 * user that passing their own fingerprint is not recommended.
 */
export function checkCustomFingerprint(fingerprint: Config): void {
	const navigator = pyTruthy(fingerprint.navigator)
		? fingerprint.navigator
		: {};
	const userAgent: string = pyTruthy(navigator.userAgent)
		? pyStr(navigator.userAgent)
		: "";
	if (
		!userAgent.includes("Chrome/") ||
		/Edg\/|OPR\/|Firefox\//.test(userAgent)
	) {
		throw new NonChromeFingerprint(
			"This fingerprint is not from Chrome " +
				`(user agent: ${pyStrRepr(userAgent || "none")}). Camouwolf is Chromium; a fingerprint from another ` +
				'browser WILL lead to detection. Generate one with fpgen and browser="Chrome".',
		);
	}
	LeakWarning.warn(loadWarnings().custom_fingerprint, false);
}

/**
 * The Chromium version ('152.0.7977.42') of the build about to launch.
 *
 * The named executable's own version.json, or what the binary answers to
 * --version; the installed release when no executable is named. A build that
 * states no version throws, since claiming another build's would leak in the
 * user agent and the client hints.
 */
export function resolveVersion(executablePath?: string | null): string {
	if (!executablePath) return utilsDeps.installedVerStr().split("-", 1)[0];
	const versionFile = buildFile(executablePath, "version.json");
	if (fs.existsSync(versionFile)) {
		const version = JSON.parse(fs.readFileSync(versionFile, "utf8"))?.version;
		if (typeof version === "string" && /^\d+(\.\d+){1,3}$/.test(version))
			return version;
	}
	const result = spawnSync(executablePath, ["--version"], {
		encoding: "utf8",
		timeout: 30_000,
	});
	if (result.error) {
		throw new ValueError(
			`Could not read the Chromium version of ${executablePath}: ${result.error.message}`,
			{ cause: result.error },
		);
	}
	const match = /\d+\.\d+\.\d+\.\d+/.exec(result.stdout ?? "");
	if (!match) {
		throw new ValueError(
			`${executablePath} states no Chromium version: it has no version.json beside it and ` +
				"answered --version with nothing recognisable.",
		);
	}
	return match[0];
}

/**
 * The navigator.keyboard.getLayoutMap() of a US QWERTY keyboard on the target
 * OS, as "code=key" entries. null on macOS, where the host's map is left as it
 * is.
 */
export function keyboardLayoutMap(targetOs: string): string[] | null {
	if (targetOs === "mac") return null;
	return [
		...Array.from(
			"abcdefghijklmnopqrstuvwxyz",
			(c) => `Key${c.toUpperCase()}=${c}`,
		),
		...Array.from("0123456789", (d) => `Digit${d}=${d}`),
		"Backquote=`",
		"Minus=-",
		"Equal==",
		"BracketLeft=[",
		"BracketRight=]",
		"Backslash=\\",
		"Semicolon=;",
		"Quote='",
		"Comma=,",
		"Period=.",
		"Slash=/",
		targetOs === "win" ? "IntlBackslash=\\" : "IntlBackslash=<",
	];
}

/** Chrome's default language list per first language (data/chrome-languages.json). */
export const CHROME_LANGUAGES: {
	readonly fallback: string;
	readonly lists: Readonly<Record<string, string[]>>;
} = loadDataFile("data/chrome-languages.json");

/**
 * The language list for a resolved locale: every listed locale, or Chrome's
 * own default list for the primary one (data/chrome-languages.json).
 *
 * The default is the list an off-the-record profile serves whatever the user
 * set, so a Playwright context and a persistent one claim the same list.
 */
export function languagesFor(localeConfig: Config): string[] | null {
	const listed = localeConfig["locale:all"];
	if (pyTruthy(listed)) {
		return String(listed)
			.split(",")
			.map((tag) => tag.trim())
			.filter((tag) => tag);
	}
	const language = localeConfig["locale:language"];
	const region = localeConfig["locale:region"];
	if (!pyTruthy(language)) return null;
	const primary: string = pyTruthy(region) ? `${language}-${region}` : language;
	// language_prefs.cc GetIncognitoLanguageList: a first language the table
	// lacks is followed by the en-US list.
	const known = CHROME_LANGUAGES.lists[primary];
	return known
		? [...known]
		: [primary, ...CHROME_LANGUAGES.lists[CHROME_LANGUAGES.fallback]];
}

/**
 * Merge `prefs` into the profile's Default/Preferences file.
 *
 * Keys are Chromium's dotted pref paths ("credentials_enable_service",
 * "profile.default_content_setting_values.notifications") or nested objects; a
 * dotted key is expanded into the nested form Chromium stores.
 */
export function writeChromiumPrefs(userDataDir: string, prefs: Config): string {
	const file = path.join(userDataDir, "Default", "Preferences");
	let current: Config;
	try {
		current = JSON.parse(fs.readFileSync(file, "utf8"));
	} catch {
		current = {};
	}
	const merge = (target: Config, source: Config): void => {
		for (const [key, value] of Object.entries(source)) {
			if (isPlainObject(value) && isPlainObject(target[key]))
				merge(target[key], value);
			else target[key] = value;
		}
	};
	for (const [key, value] of Object.entries(prefs)) {
		let nested: any = value;
		for (const part of key.split(".").reverse()) nested = { [part]: nested };
		merge(current, nested);
	}
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, JSON.stringify(current));
	return file;
}

/*
 * What a set of launch options carries
 */

/** The CAMOU_CONFIG_<n> chunks of a set of launch options, joined in order. */
export function camouConfigBlob(fromOptions: Config): string {
	const env: Record<string, any> = fromOptions.env ?? {};
	return Object.entries(env)
		.filter(([k]) => /^CAMOU_CONFIG_\d+$/.test(k))
		.map(
			([k, v]) =>
				[
					Number.parseInt(k.slice(k.lastIndexOf("_") + 1), 10),
					String(v),
				] as const,
		)
		.sort((a, b) => a[0] - b[0])
		.map(([, v]) => v)
		.join("");
}

/** The config a set of launch options carries in its environment. */
export function launchConfig(fromOptions: Config): Config {
	const blob = camouConfigBlob(fromOptions);
	if (!blob) return {};
	try {
		const config = JSON.parse(blob);
		return isPlainObject(config) ? config : {};
	} catch {
		return {};
	}
}

/**
 * The OS ('macos', 'windows' or 'linux') of the identity these launch options
 * claim, the host's when they claim no user agent.
 */
export function launchOs(fromOptions: Config): OsName {
	return OS_NAMES[getTargetOs(launchConfig(fromOptions))] as OsName;
}

export interface GeolocationOption {
	latitude: number;
	longitude: number;
	accuracy?: number;
}

/**
 * The position these launch options place the browser at, as Playwright's
 * `geolocation` option, or null.
 */
export function launchGeolocation(
	fromOptions: Config,
): GeolocationOption | null {
	const config = launchConfig(fromOptions);
	const latitude = config["geolocation:latitude"];
	const longitude = config["geolocation:longitude"];
	if (
		latitude === null ||
		latitude === undefined ||
		longitude === null ||
		longitude === undefined
	) {
		return null;
	}
	const geolocation: GeolocationOption = { latitude, longitude };
	if (pyTruthy(config["geolocation:accuracy"]))
		geolocation.accuracy = config["geolocation:accuracy"];
	return geolocation;
}

/**
 * The core count the browser must be pinned to for these launch options, or
 * null: the identity's navigator.hardwareConcurrency when this host can honour
 * it (see cpu_affinity), so a page measuring parallelism sees the reported
 * number.
 */
export function pinnedCoreCount(fromOptions: Config): number | null {
	if (!cpuAffinity.supported()) return null;
	const value = launchConfig(fromOptions)["navigator.hardwareConcurrency"];
	// The host's count, not this process's live mask: a concurrent pinned
	// launch narrows that while its browser spawns.
	const cores = cpuAffinity.hostCoreCount();
	if (
		typeof value === "number" &&
		Number.isInteger(value) &&
		cores &&
		value >= 1 &&
		value < cores
	) {
		return value;
	}
	return null;
}

/**
 * PID of the process that will spawn the browser (its children inherit the
 * CPU affinity set on it). Python's driver is a separate Node process;
 * playwright-core runs in this one.
 */
export function driverPid(): number {
	return process.pid;
}

/*
 * Context defaults
 */

/**
 * Playwright emulates four media features on every context it creates, whether
 * or not the caller asked: `colorScheme` defaults to "light" and reducedMotion
 * / forcedColors / contrast to their no-preference values. That is an
 * override, not a passthrough: the page then reports it whatever the host is
 * set to. `null` is Playwright's own opt-out (Python's "no-override"): it
 * sends no emulation at all and the browser answers for itself.
 */
export const STOCK_MEDIA_DEFAULTS = {
	colorScheme: null,
	reducedMotion: null,
	forcedColors: null,
	contrast: null,
} as const;

/**
 * Context options that would make one context of the browser a different
 * machine from the rest of it. The identity is the process's: workers, the
 * network stack and the GPU process answer from CAMOU_CONFIG whatever
 * Playwright emulates in one context's pages, so a page comparing its window
 * with a worker reads two user agents. Named as Playwright's JS API names
 * them (Python: user_agent, is_mobile, device_scale_factor, screen, has_touch).
 */
export const PER_CONTEXT_IDENTITY_OPTIONS: Readonly<Record<string, string>> = {
	userAgent:
		"navigator.userAgent comes from the launch identity; set `config`, `os` or `fingerprint` at launch",
	isMobile: "Camouwolf only has desktop identities",
	deviceScaleFactor:
		"it changes the device pixel ratio without the screen the identity claims",
	screen:
		"the screen comes from the launch identity; use the `screen` launch option",
	hasTouch: "the launch identity reports no touchscreen",
};

/**
 * The context options in `options` that would give one context another
 * identity than the browser's, each with the reason.
 */
export function perContextIdentityProblems(options: Config): string[] {
	return Object.entries(PER_CONTEXT_IDENTITY_OPTIONS)
		.filter(
			([option]) => options[option] !== null && options[option] !== undefined,
		)
		.map(([option, why]) => `${option}= cannot be set per context: ${why}`);
}

/**
 * Throw IncoherentIdentity for a context option that would give one context
 * another identity than the browser's.
 */
export function refusePerContextIdentity(options: Config): void {
	const problems = perContextIdentityProblems(options);
	if (problems.length) {
		throw new IncoherentIdentity(
			`${problems.join("; ")}. Workers, request headers and the GPU process would still answer as the launch ` +
				"identity. Launch another browser for another identity.",
		);
	}
}

/**
 * The adjustment every newPage()/newContext() of a Camouwolf browser gets.
 *
 * - no viewport: Playwright's default 1280x720 viewport is a device-metrics
 *   override, which replaces the window size the identity launched with and
 *   reports the viewport as the screen. Without it the page reads the real
 *   window. A caller's `viewport` wins.
 * - the host's own media features instead of Playwright's emulated ones.
 * - the launch's geolocation (geoip, or geolocation:* in config), granted, as
 *   a browser whose user chose "Always allow" has it.
 * - a LeakWarning for an option that gives the context another identity than
 *   the browser's (NewContext refuses those outright).
 *
 * Returns the adjusted copy of the options.
 */
export function contextDefaults(
	geolocation: GeolocationOption | null | undefined,
): (options?: Config) => Config {
	return (options?: Config): Config => {
		const out: Config = { ...options };
		for (const problem of perContextIdentityProblems(out)) {
			warn(
				loadWarnings().per_context_identity.replaceAll(
					"{problem}",
					() => problem,
				),
				"LeakWarning",
			);
		}
		// Python's no_viewport=True is `viewport: null` in Playwright's JS API.
		if ("noViewport" in out) {
			const noViewport = out.noViewport;
			delete out.noViewport;
			if (noViewport && !("viewport" in out)) out.viewport = null;
		} else if (!("viewport" in out)) {
			out.viewport = null;
		}
		for (const [option, value] of Object.entries(STOCK_MEDIA_DEFAULTS)) {
			if (!(option in out)) out[option] = value;
		}
		if (geolocation && !("geolocation" in out)) {
			out.geolocation = { ...geolocation };
			const permissions: string[] = [...(out.permissions ?? [])];
			if (!permissions.includes("geolocation")) permissions.push("geolocation");
			out.permissions = permissions;
		}
		return out;
	};
}

/** Camoufox's NewContext options that draw a second identity for one context. */
export const PER_CONTEXT_FINGERPRINT_OPTIONS: readonly string[] = [
	"preset",
	"os",
	"ff_version",
	"chrome_version",
	"webrtc_ip",
];

/**
 * A proxied context's timezone and position, defaulted to where the proxy
 * exits (looked up through it) unless the caller gave both. A failed lookup
 * throws: the context would otherwise sit at the launch's place beside another
 * IP. The locale stays the identity's, which patch 0026 holds.
 */
/**
 * Whether this host has a public IPv6 address that geolocates where the IPv4
 * identity does. Warns when it has one that does not.
 */
export async function keepsIpv6(
	geoConfig: Config,
	geoipDb: string | undefined,
	iKnowWhatImDoing: boolean | undefined,
): Promise<boolean> {
	const ipv6 = await utilsDeps.publicIpv6();
	if (ipv6 === null) return false;
	let ipv6Config: Config = {};
	try {
		ipv6Config = (await utilsDeps.getGeolocation(ipv6, geoipDb)).asConfig();
	} catch (error) {
		if (!(error instanceof UnknownIPLocation)) throw error;
	}
	if (
		["timezone", "locale:region"].every(
			(key) => ipv6Config[key] === geoConfig[key],
		)
	)
		return true;
	LeakWarning.warn(loadWarnings().ipv6_elsewhere, iKnowWhatImDoing);
	return false;
}

export async function placeAtExit(
	proxy: ProxyConfig | null | undefined,
	geolocation: GeolocationOption | null | undefined,
	contextOptions: Config,
): Promise<[GeolocationOption | null | undefined, Config]> {
	if (!proxy || (geolocation && "timezoneId" in contextOptions))
		return [geolocation, contextOptions];
	utilsDeps.geoipAllowed();
	const exitGeo = await utilsDeps.getGeolocation(
		await utilsDeps.publicIp(ProxyHelper.asString(proxy)),
	);
	return [
		geolocation ?? {
			latitude: exitGeo.latitude,
			longitude: exitGeo.longitude,
		},
		{ timezoneId: exitGeo.timezone, ...contextOptions },
	];
}

/**
 * The newContext() options NewContext passes: the caller's, after refusing any
 * that would give the context an identity of its own.
 */
export function newContextOptions(
	contextOptions: Config,
	proxy?: ProxyConfig | null,
	geolocation?: GeolocationOption | null,
): Config {
	for (const option of PER_CONTEXT_FINGERPRINT_OPTIONS) {
		if (
			contextOptions[option] !== null &&
			contextOptions[option] !== undefined
		) {
			throw new NotImplementedError(
				`NewContext(${option}=) is not implemented in Camouwolf: the browser reads one identity ` +
					"per process (CAMOU_CONFIG), so a context cannot have its own fingerprint. Launch " +
					"another browser for another identity.",
			);
		}
	}
	refusePerContextIdentity(contextOptions);
	const options: Config = { ...contextOptions };
	if (proxy) options.proxy = proxy;
	if (geolocation) {
		options.geolocation = geolocation;
		const permissions: string[] = [...(options.permissions ?? [])];
		if (!permissions.includes("geolocation")) permissions.push("geolocation");
		options.permissions = permissions;
	}
	return options;
}

/**
 * Launch options for launchPersistentContext(). The launch itself creates the
 * context, so the defaults newContext() would get go into the options.
 */
export function persistentContextOptions(fromOptions: Config): Config {
	return contextDefaults(launchGeolocation(fromOptions))(fromOptions);
}

/**
 * Warn that a non-persistent launch carries extensions its contexts will not
 * run.
 */
export function warnAddonsWithoutProfile(fromOptions: Config): void {
	const args: unknown[] = fromOptions.args ?? [];
	if (args.some((arg) => String(arg).startsWith("--load-extension="))) {
		warn(loadWarnings().addons_need_persistent_context, "RuntimeWarning");
	}
}

/** Give every newPage()/newContext() of `browser` Camouwolf's defaults. */
export function attachContextDefaults<T>(browser: T, fromOptions: Config): T {
	const adjust = contextDefaults(launchGeolocation(fromOptions));
	for (const name of ["newPage", "newContext"] as const) {
		const original = (browser as any)[name];
		if (typeof original !== "function") continue;
		(browser as any)[name] = (options?: Config, ...rest: any[]) =>
			original.call(browser, adjust(options), ...rest);
	}
	attachDriverDefaults(browser);
	return browser;
}

/*
 * Virtual displays
 */

/** The headless modes NewBrowser and launchServer accept. */
export type Headless = boolean | "virtual" | "virtual-gpu";

/** The options of the display headless="virtual-gpu" starts. */
export interface GpuDisplayOptions {
	/** The GPU for headless="virtual-gpu": an index, a render or card node, a
	 * PCI address, vendor:device ids or a vendor name. Defaults to
	 * $CAMOUWOLF_GPU, or the host's only GPU. */
	gpu?: string | number;
	/** Fail the launch (the default) when the browser on the GPU display does
	 * not render on the GPU; false warns instead. */
	require_gpu?: boolean;
}

/**
 * Start the display headless="virtual" or "virtual-gpu" asks for.
 *
 * Takes the display's own options out of `options` and points the launch at
 * the display. Returns the display, or null, and the headless value to launch
 * with.
 */
export async function startVirtualDisplay(
	headless: Headless | undefined | null,
	debug: boolean | undefined | null,
	options: LaunchOptions & GpuDisplayOptions,
): Promise<[VirtualDisplay | null, boolean | undefined | null]> {
	const { gpu, require_gpu } = options;
	delete options.gpu;
	delete options.require_gpu;
	let display: VirtualDisplay;
	if (headless === "virtual-gpu") {
		display = new GpuDisplay(
			utilsDeps.selectGpu(gpu),
			Boolean(debug),
			require_gpu !== false,
		);
		// The caller's own switches come after, and win.
		options.args = [...GPU_SWITCHES, ...(options.args ?? [])];
	} else if (
		(gpu !== undefined && gpu !== null) ||
		(require_gpu !== undefined && require_gpu !== null)
	) {
		throw new ValueError(
			"gpu and require_gpu apply to headless='virtual-gpu' only",
		);
	} else if (headless === "virtual") {
		display = new VirtualDisplay(PRODUCT, Boolean(debug));
	} else {
		return [null, headless as boolean | undefined | null];
	}
	// launchOptions sizes the display to the identity and starts it.
	options.virtual_display = display;
	return [display, false];
}

/**
 * Attaches the virtual display to the browser's cleanup, and on a GPU display
 * checks the browser renders on the GPU. (Python has an async and a sync
 * variant; playwright-core has one API.)
 */
export async function attachVirtualDisplay<T>(
	browser: T,
	virtualDisplay?: VirtualDisplay | null,
): Promise<T> {
	if (!virtualDisplay) return browser; // Skip if no virtual display is provided

	const target = browser as any;
	const originalClose = target.close.bind(target);

	target.close = async (...args: any[]) => {
		try {
			return await originalClose(...args);
		} finally {
			virtualDisplay.kill();
		}
	};
	// close() never runs on an unexpected disconnect/close, so wire teardown
	// directly too; kill() is idempotent.
	target.on?.(target.contexts ? "disconnected" : "close", () =>
		virtualDisplay.kill(),
	);
	target._virtualDisplay = virtualDisplay;

	if (virtualDisplay instanceof GpuDisplay) {
		try {
			await proveGpu(target, virtualDisplay);
		} catch (error) {
			await target.close();
			throw error;
		}
	}
	return browser;
}

/*
 * The launch
 */

/** A bound on the screen a generated fingerprint may claim. */
export type ScreenConstraint =
	| Screen
	| {
			min_width?: number;
			max_width?: number;
			min_height?: number;
			max_height?: number;
			minWidth?: number;
			maxWidth?: number;
			minHeight?: number;
			maxHeight?: number;
	  };

function toScreen(screen: ScreenConstraint): Screen {
	if (screen instanceof Screen) return screen;
	const s = screen as Record<string, number | undefined>;
	return new Screen({
		minWidth: s.minWidth ?? s.min_width,
		maxWidth: s.maxWidth ?? s.max_width,
		minHeight: s.minHeight ?? s.min_height,
		maxHeight: s.maxHeight ?? s.max_height,
	});
}

/** repr() of a list of (vendor, renderer) tuples. */
function reprPairs(pairs: ReadonlyArray<readonly [string, string]>): string {
	return `[${pairs.map(([v, r]) => `(${pyStrRepr(v)}, ${pyStrRepr(r)})`).join(", ")}]`;
}

/** An fpgen condition on navigator.languages: the first is `language`'s. */
export function speaks(language: string): (value: unknown) => boolean {
	const wanted = language.split("-")[0].toLowerCase();
	return (value) =>
		Array.isArray(value) &&
		value.length > 0 &&
		pyStr(value[0]).split("-")[0].toLowerCase() === wanted;
}

/**
 * Draw an fpgen Chrome fingerprint whose GPU the identity may claim.
 *
 * Without `webglConfig`, any recorded hardware GPU on a backend its OS uses.
 * With it, a record of exactly that device: its limits, screen and fonts are
 * then the ones a machine with that GPU reported.
 *
 * With `language`, a record of Chrome in that language where fpgen has one:
 * a machine's voices and fonts follow the language its OS was installed in.
 */
export async function drawFingerprint(
	os: ListOrString | null | undefined,
	screen: Screen | null | undefined,
	window: readonly [number, number] | null | undefined,
	webglConfig: readonly [string, string] | null | undefined,
	language?: string,
): Promise<Config> {
	await utilsDeps.ensureModel();
	const names: string[] = typeof os === "string" ? [os] : [...(os ?? [])];
	let gpu: (value: unknown) => boolean;
	if (webglConfig?.length) {
		const [vendor, renderer] = webglConfig;
		const has = (name: string) =>
			utilsDeps
				.chromeGpus(name)
				.some(([v, r]) => v === vendor && r === renderer);
		if (!names.some(has)) {
			const possible = names
				.flatMap((name) => utilsDeps.chromeGpus(name))
				.sort((a, b) => comparePyStr(a[0], b[0]) || comparePyStr(a[1], b[1]));
			throw new ValueError(
				`No recorded Chrome device with vendor ${pyRepr(vendor)} and renderer ${pyRepr(renderer)} on ` +
					`${names.join(", ")}. Possible pairs: ${reprPairs(possible)}`,
			);
		}
		// fpgen hands a predicate the recorded value lowercased.
		const wantedVendor = vendor.toLowerCase();
		const wantedRenderer = renderer.toLowerCase();
		gpu = (value) =>
			isPlainObject(value) &&
			pyStr(value.vendor ?? null).toLowerCase() === wantedVendor &&
			pyStr(value.renderer ?? null).toLowerCase() === wantedRenderer;
	} else {
		gpu = (value) => usableGpu(value);
	}

	const conditions: Record<string, unknown> = { gpu };
	if (language) conditions.languages = speaks(language);
	for (let i = 0; i < MAX_FINGERPRINT_DRAWS; i++) {
		let fingerprint: Config;
		try {
			fingerprint = utilsDeps.generateFingerprint({
				window,
				screen,
				os: pyTruthy(os) ? os : null,
				...conditions,
			});
		} catch (err) {
			if (!("languages" in conditions)) throw err;
			// fpgen has no record of Chrome in this language on these machines
			// (its sampler throws a TypeError on a pairing it never recorded).
			// The draw is then a machine installed in another language.
			delete conditions.languages;
			continue;
		}
		const drawnOs = fingerprintOs(fingerprint);
		// Camouwolf builds are x86-64 desktops. fpgen also records Chrome on ARM
		// Linux ("Linux armv81", "Linux aarch64"), whose platform a build that
		// computes as x86-64 cannot back up.
		const navigator = pyTruthy(fingerprint.navigator)
			? fingerprint.navigator
			: {};
		const platform = navigator.platform ?? null;
		const expected = drawnOs ? clienthints.NAV_PLATFORM[drawnOs] : null;
		if (platform !== expected) continue;
		if (!usableGpu(fingerprint.gpu, drawnOs)) continue;
		const drawnScreen = pyTruthy(fingerprint.screen) ? fingerprint.screen : {};
		if (
			coherence.machineMisfit(
				fingerprint.gpu.renderer,
				drawnOs ?? "",
				navigator.hardwareConcurrency,
				drawnScreen.width,
				drawnScreen.height,
			)
		)
			continue;
		return fingerprint;
	}
	throw new Error(
		`fpgen drew no desktop Chrome fingerprint with a usable GPU for os=${pyRepr(os ?? null)} in ` +
			`${MAX_FINGERPRINT_DRAWS} draws`,
	);
}

export interface LaunchOptions {
	/** Camouwolf properties to use; they win over generated values. An unknown
	 * key throws UnknownProperty (see data/properties.json). */
	config?: Record<string, any>;
	/** Operating system of the identity: "windows", "macos", "linux", or a list
	 * to choose from. Defaults to what this host can pass for: its own OS, and
	 * Windows on a Linux host. Another OS throws UnsupportedOSSpoof unless
	 * `allow_os_mismatch` is true. */
	os?: string | string[];
	/** Whether to block all images. */
	block_images?: boolean;
	/** Keep WebRTC to TCP through the proxy, with no UDP. Without it, a
	 * proxied launch's WebRTC is a machine's at the proxy's exit: UDP goes
	 * through a SOCKS5 proxy's relay, and through no other proxy. */
	block_webrtc?: boolean;
	/** Whether to block WebGL. To prevent leaks, only use this for special cases. */
	block_webgl?: boolean;
	/** Not implemented: throws NotImplementedError. */
	disable_coop?: boolean;
	/** Use a specific WebGL vendor/renderer pair. It must be one fpgen has
	 * recorded from Chrome on `os` (chromeGpus); the whole identity is then
	 * drawn from a machine with that GPU. */
	webgl_config?: [string, string];
	/** Not implemented: throws NotImplementedError. */
	cpu?: Record<string, string>;
	/** Calculate longitude, latitude, timezone, country & locale from the IP
	 * address. Pass the target IP, or `true` to find it automatically. */
	geoip?: string | boolean;
	/** Name of the GeoIP database to use (e.g. "GeoIP AIO by daijro"). */
	geoip_db?: string;
	/** Driver-side humanization. Applied by Camouwolf()/NewBrowser(); only
	 * validated here. */
	humanize?: HumanizeSetting;
	/** Locale(s) to use. The first is the browser's UI and Intl locale; all of
	 * them are navigator.languages and the Accept-Language header. One locale
	 * claims Chrome's default list for it ("de-DE" is de-DE, de, en-US, en). A
	 * non-persistent context serves that default for the first of a longer
	 * list, as Chrome does off the record. */
	locale?: string | string[];
	/** Unpacked Chrome extensions (directories holding a manifest.json). */
	addons?: string[];
	/** Font families to report installed, in addition to the identity's own. */
	fonts?: string[];
	/** Report only `fonts` installed, without the identity's own. */
	custom_fonts_only?: boolean;
	/** Default addons to exclude. */
	exclude_addons?: DefaultAddon[];
	/** Constrains the screen dimensions of the generated fingerprint. */
	screen?: ScreenConstraint;
	/** Set a fixed outer window size instead of the fingerprint's. */
	window?: [number, number];
	/** An fpgen Chrome fingerprint to use instead of drawing one. */
	fingerprint?: Record<string, any>;
	/** Not implemented: throws NotImplementedError. */
	fingerprint_preset?: boolean | Record<string, any>;
	/** Chromium milestone to claim. Defaults to the browser's own; another one
	 * disagrees with what the binary can do, and warns. */
	chrome_version?: number;
	/** Camoufox's option; throws NotImplementedError naming chrome_version. */
	ff_version?: number;
	/** Whether to run Chromium's own headless mode. Defaults to false. */
	headless?: boolean | null;
	/** Accepted for Camoufox compatibility; nothing to switch on. */
	main_world_eval?: boolean;
	/** Not implemented: throws NotImplementedError when set. */
	allow_addon_new_tab?: boolean;
	/** Custom Camouwolf browser executable path. CAMOUWOLF_EXECUTABLE_PATH in
	 * the environment does the same. */
	executable_path?: string;
	/** Select a specific installed browser version ("official/alpha.1", ...). */
	browser?: string;
	/** Chromium preferences, merged into the profile's Preferences file before
	 * launch. Needs a persistent context (`user_data_dir`). */
	chromium_prefs?: Record<string, any>;
	/** Camoufox's option; throws NotImplementedError. */
	firefox_user_prefs?: Record<string, any>;
	/** Proxy to use for the browser. */
	proxy?: ProxyConfig;
	/** Keep the HTTP disk cache. Off by default. */
	enable_cache?: boolean;
	/** Arguments to pass to the browser. */
	args?: string[];
	/** Environment variables to set. */
	env?: EnvVars;
	/** Silence warnings, and drop conflicting switches instead of refusing them. */
	i_know_what_im_doing?: boolean;
	/** Prints the config being sent to Camouwolf. */
	debug?: boolean | null;
	/**
	 * Virtual display number. Ex: ':99'. Handled by Camouwolf(), which passes
	 * a display not yet started; it is sized to the identity and started here.
	 */
	virtual_display?: string | VirtualDisplay;
	/**
	 * The virtual display's size in device pixels; the identity's screen, at
	 * its devicePixelRatio, is kept within it.
	 */
	virtual_display_size?: [number, number];
	/** Keep the fingerprint's own core count by pinning the browser to that
	 * many cores. Off by default: the host's count is reported. */
	pin_cpu_cores?: boolean;
	/** Perturb canvas readback with a per-identity seed. Off by default. */
	canvas_noise?: boolean | null;
	/** Allow an identity on an OS this host cannot pass for. */
	allow_os_mismatch?: boolean;
	/** Additional Playwright Chromium launch options. */
	[key: string]: any;
}

/**
 * Builds the Playwright launch options for a Camouwolf browser. Accepts all
 * Playwright Chromium launch options, along with those of LaunchOptions.
 */
export async function launchOptions({
	config,
	os,
	block_images,
	block_webrtc,
	block_webgl,
	disable_coop,
	webgl_config,
	cpu,
	geoip,
	geoip_db,
	humanize,
	locale,
	addons,
	fonts,
	custom_fonts_only,
	exclude_addons,
	screen,
	window,
	fingerprint,
	fingerprint_preset,
	chrome_version,
	ff_version,
	headless,
	main_world_eval: _main_world_eval,
	allow_addon_new_tab,
	executable_path,
	browser,
	chromium_prefs,
	firefox_user_prefs,
	proxy,
	enable_cache,
	args,
	env,
	i_know_what_im_doing,
	debug,
	virtual_display,
	virtual_display_size,
	pin_cpu_cores,
	canvas_noise,
	allow_os_mismatch,
	...passthrough
}: LaunchOptions = {}): Promise<Record<string, any>> {
	// ---- options with no Chromium equivalent ------------------------------
	if (pyTruthy(firefox_user_prefs)) {
		throw new NotImplementedError(
			"firefox_user_prefs has no Chromium equivalent. Pass command-line switches in `args`, or " +
				"Chromium preferences in `chromium_prefs` together with persistent_context=True and a user_data_dir.",
		);
	}
	if (ff_version !== undefined && ff_version !== null) {
		throw new NotImplementedError(
			"ff_version is Camoufox's option. Use chrome_version (a Chromium milestone).",
		);
	}
	if (cpu !== undefined && cpu !== null) {
		throw new NotImplementedError(
			"cpu= is not implemented in Camouwolf: the browser has no cpu:arch / cpu:manufacturer " +
				"keys and computes as the host CPU does.",
		);
	}
	if (pyTruthy(fingerprint_preset)) {
		throw new NotImplementedError(
			"fingerprint_preset is not implemented in Camouwolf: no presets are bundled. " +
				"Leave it unset to draw an fpgen fingerprint, or pass one as `fingerprint`.",
		);
	}
	if (disable_coop) {
		throw new NotImplementedError(
			"disable_coop is not implemented in Camouwolf: Chromium has no switch for it, and none is " +
				"needed. Playwright reaches elements inside cross-origin iframes on Chromium as it is.",
		);
	}
	if (allow_addon_new_tab) {
		throw new NotImplementedError(
			"allow_addon_new_tab is not implemented in Camouwolf: the launcher does not stop an " +
				"extension from opening a tab, so there is nothing to allow.",
		);
	}
	// main_world_eval: nothing to switch on. Playwright evaluates in the page's
	// main world on Chromium.

	// The launch fills these in, so it works on copies: a caller's object
	// reused for a second launch would otherwise carry the first identity's
	// values in, as if the caller had set them.
	config = { ...config };
	addons = [...(addons ?? [])];
	args = [...(args ?? [])];

	headless ??= false;
	i_know_what_im_doing ??= false;
	normalize(humanize);

	// Keep per-launch overrides isolated from the process environment and from
	// mappings supplied by callers. In particular, DISPLAY must not outlive the
	// virtual display that owns it.
	const environ = utilsDeps.environ();
	env = env == null ? ({ ...environ } as EnvVars) : { ...env };
	utilsDeps.ensureBrowserProfileDir(env);

	// ---- the binary --------------------------------------------------------
	// CAMOUWOLF_EXECUTABLE_PATH points every launch at a specific build without
	// threading the path through each call site.
	const envExecutable = (environ.CAMOUWOLF_EXECUTABLE_PATH ?? "").trim();
	if (browser) {
		if (executable_path || envExecutable) {
			throw new ValueError(
				`browser=${pyRepr(browser)} selects an installed build, but ` +
					`${executable_path ? "executable_path" : "CAMOUWOLF_EXECUTABLE_PATH"} ` +
					"names a binary too. Pass one of them.",
			);
		}
		executable_path = utilsDeps.selectedLaunchPath(browser);
	} else if (executable_path == null && envExecutable) {
		executable_path = envExecutable;
	}
	let browserBinary: string;
	if (executable_path != null) {
		browserBinary = path.resolve(String(executable_path));
	} else {
		// Python's launch_path() downloads the browser when none is installed;
		// here that download is async, so it is awaited first.
		await utilsDeps.ensureCamouwolfInstalled();
		browserBinary = utilsDeps.launchPath();
	}
	utilsDeps.warnIfPackageOutdated();

	// ---- display -----------------------------------------------------------
	const ownedDisplay =
		virtual_display instanceof VirtualDisplay ? virtual_display : null;
	let displaySize = virtual_display_size;
	if (ownedDisplay?.fixed) displaySize = ownedDisplay.size;
	if (virtual_display) {
		if (!ownedDisplay) env.DISPLAY = virtual_display as string;
		// The virtual display is X11. A host session that exports
		// WAYLAND_DISPLAY would otherwise send the window to the real desktop.
		env.GDK_BACKEND = "x11";
		delete env.WAYLAND_DISPLAY;
		args.unshift("--ozone-platform=x11");
	}

	// ---- the identity ------------------------------------------------------
	if (!i_know_what_im_doing) warnManualConfig(config);
	const callerKeys: ReadonlySet<string> = new Set(Object.keys(config));
	const userSetScreenWindow = isDomainSet(config, "screen.", "window.");

	let osOption: ListOrString | null | undefined = os;
	if (pyTruthy(osOption)) {
		checkValidOs(osOption as ListOrString);
		checkOsPolicy(
			typeof osOption === "string" ? [osOption] : [...(osOption as string[])],
			allow_os_mismatch,
		);
	} else if (pyTruthy(webgl_config)) {
		throw new ValueError("OS must be set when using webgl_config");
	} else if (!allow_os_mismatch) {
		osOption = [...ALLOWED_OS_TARGETS[utilsDeps.osName()]];
	}

	// The identity has to match the Chromium that actually runs.
	let fullVersion = resolveVersion(browserBinary);
	const ownMilestone = Number.parseInt(fullVersion.split(".", 1)[0], 10);
	if (
		chrome_version !== undefined &&
		chrome_version !== null &&
		Math.trunc(Number(chrome_version)) !== ownMilestone
	) {
		LeakWarning.warn(loadWarnings().chrome_version, i_know_what_im_doing);
		fullVersion = `${Math.trunc(Number(chrome_version))}.0.0.0`;
	}
	const milestone = Number.parseInt(fullVersion.split(".", 1)[0], 10);

	// A real display bounds the screen the identity may claim; so does a
	// virtual one of fixed size, which the real window has to fit on. One the
	// launcher sizes is fitted to the identity instead.
	let displayBound: Screen | null = null;
	if (headless !== true) {
		if (pyTruthy(displaySize)) {
			const [width, height] = displaySize as [number, number];
			displayBound = new Screen({ maxWidth: width, maxHeight: height });
		} else if (!virtual_display && utilsDeps.hasDisplay(env)) {
			const display = utilsDeps.largestDisplay();
			if (display) {
				displayBound = new Screen({
					maxWidth: display.width,
					maxHeight: display.height,
				});
			}
		}
	}

	// ---- place: geoip, locale ---------------------------------------------
	let localeConfig: Config = {};
	if (pyTruthy(geoip)) {
		utilsDeps.geoipAllowed(); // Assert that geoip is allowed
		let ip = geoip as string | true;
		if (ip === true) {
			ip = await utilsDeps.publicIp(
				proxy ? ProxyHelper.asString(proxy) : undefined,
			);
		}
		const geoConfig = (await utilsDeps.getGeolocation(ip, geoip_db)).asConfig();
		for (const [key, value] of Object.entries(geoConfig)) {
			if (key.startsWith("locale:")) localeConfig[key] = value;
			else if (key === "timezone") setDefault(config, key, value);
			else config[key] = value;
		}
		// Without a proxy, a site that has an IPv6 address sees this host's,
		// not the IPv4 address the identity was drawn from. Keep IPv6 only when
		// it places the host where the identity does.
		if (
			!proxy &&
			validIPv4(ip) &&
			!(await keepsIpv6(geoConfig, geoip_db, i_know_what_im_doing))
		)
			config["network:ipv4Only"] = true;
		// Behind a proxy, WebRTC sees the network of a machine at the exit
		// (patch 0031): its one adapter has the exit's address.
		if (pyTruthy(proxy) && !block_webrtc)
			setDefault(config, validIPv4(ip) ? "webrtc:ipv4" : "webrtc:ipv6", ip);
	} else if (
		pyTruthy(proxy) &&
		!String(proxy?.server ?? "").includes("localhost") &&
		!String(proxy?.server ?? "").includes("127.0.0.1") &&
		!isDomainSet(config, "geolocation:")
	) {
		// This is a very bad idea; the warning cannot be silenced.
		LeakWarning.warn(loadWarnings().proxy_without_geoip);
	}

	if (pyTruthy(locale)) {
		localeConfig = {};
		await handleLocales(locale as string | string[], localeConfig);
	}
	// Chrome serves navigator.languages and Accept-Language from one pref, which
	// --accept-lang sets, so the config does not repeat it: an off-the-record
	// context swaps the pref's list, and a pinned copy would then disagree.
	const languages: string[] | null = pyTruthy(config["navigator.languages"])
		? config["navigator.languages"]
		: languagesFor(localeConfig);

	// A caller who names the user agent themselves has pinned the identity.
	if (
		(fingerprint === undefined || fingerprint === null) &&
		!("navigator.userAgent" in config)
	) {
		fingerprint = await drawFingerprint(
			osOption,
			screen ? toScreen(screen) : displayBound,
			window,
			webgl_config,
			languages?.[0],
		);
	} else if (fingerprint !== undefined && fingerprint !== null) {
		if (pyTruthy(webgl_config)) {
			throw new ValueError(
				"webgl_config draws the fingerprint; it cannot be combined with `fingerprint`",
			);
		}
		if (!i_know_what_im_doing) checkCustomFingerprint(fingerprint);
		if (pyTruthy(window)) {
			fingerprint = {
				...fingerprint,
				screen: {
					...(pyTruthy(fingerprint.screen) ? fingerprint.screen : {}),
				},
				window: {
					...(pyTruthy(fingerprint.window) ? fingerprint.window : {}),
				},
			};
			const [width, height] = window as [number, number];
			handleWindowSize(fingerprint, width, height);
		}
	} else if (pyTruthy(webgl_config)) {
		throw new ValueError(
			"webgl_config draws the fingerprint; it cannot be combined with a user agent in `config`",
		);
	}

	if (fingerprint !== undefined && fingerprint !== null) {
		mergeInto(config, fromFpgen(fingerprint, milestone));
	}
	// Patch 0012 shows the host's voices only when the key is absent, so it is
	// always sent: the recorded list, or none, never the host's.
	if (!("voices" in config)) config.voices = [];

	const targetOs = getTargetOs(config);
	checkOsPolicy([OS_NAMES[targetOs]], allow_os_mismatch);

	setDefault(
		config,
		"navigator.userAgent",
		clienthints.buildUserAgent({ milestone, fullVersion, os: targetOs }),
	);
	setDefault(config, "navigator.platform", clienthints.NAV_PLATFORM[targetOs]);
	clienthints.fillClientHints(config, targetOs, fullVersion);

	// The core count a page can measure is the host's, unless the browser is
	// pinned to the drawn one.
	if (!callerKeys.has("navigator.hardwareConcurrency")) {
		fixHardwareConcurrency(config, Boolean(pin_cpu_cores), utilsDeps.hostCpu());
	}

	// ---- fonts -------------------------------------------------------------
	if (custom_fonts_only) {
		if (!pyTruthy(fonts)) {
			throw new ValueError("custom_fonts_only=True needs `fonts`");
		}
		LeakWarning.warn(loadWarnings().custom_fonts_only, i_know_what_im_doing);
		config.fonts = [...new Set(fonts)];
	} else if (pyTruthy(fonts)) {
		const base: string[] = pyTruthy(config.fonts)
			? config.fonts
			: ESSENTIAL_FONTS[targetOs];
		config.fonts = [...new Set([...base, ...(fonts as string[])])];
	}

	// ---- devices, keyboard, colours ---------------------------------------
	setDefault(config, "mediaDevices:enumerated", [
		...DEFAULT_MEDIA_DEVICES[targetOs],
	]);
	const layout = keyboardLayoutMap(targetOs);
	if (layout) setDefault(config, "keyboard.layoutMap", layout);
	const colors = SYSTEM_COLORS[OS_NAMES[targetOs]];
	if (pyTruthy(colors)) setDefault(config, "systemColors", { ...colors });

	// ---- canvas ------------------------------------------------------------
	if (canvas_noise) {
		LeakWarning.warn(loadWarnings().canvas_noise, i_know_what_im_doing);
		if (!("canvas:seed" in config))
			config["canvas:seed"] = utilsDeps.canvasSeed();
		setDefault(config, "canvas:randomizeReadback", true);
	} else {
		// Stock Chrome returns the pixels it drew.
		setDefault(config, "canvas:seed", 0);
		setDefault(config, "canvas:randomizeReadback", false);
	}
	setDefault(config, "canvas:quantizeTextMetrics", false);
	setDefault(config, "AudioContext:randomizeSamples", false);

	// ---- geometry ----------------------------------------------------------
	if (!userSetScreenWindow) {
		coherence.repairDevicePixelRatio(
			config,
			targetOs,
			window ? ["window.outerWidth", "window.outerHeight"] : [],
		);
		raiseScreenToModernFloor(config);
		coherence.repairScreenOrientation(config);
		if (displayBound !== null) {
			// A virtual display is measured in device pixels, and the window
			// renders at the identity's ratio.
			const scale = virtual_display
				? num(config["window.devicePixelRatio"] || 1)
				: 1;
			clampScreenToDisplay(
				config,
				Math.trunc(num(displayBound.maxWidth) / scale),
				Math.trunc(num(displayBound.maxHeight) / scale),
			);
		}
		fixScreenNoTaskbar(config, targetOs);
		clampWindowDimensions(config);
		clampWindowPosition(config);
		snapWindowToDevicePixels(config);
	}

	// ---- coherence ---------------------------------------------------------
	const violations = coherence.apply(config, targetOs, callerKeys);
	warnKeptIncoherent(violations);
	const fatal = violations.filter((v) => coherence.FATAL_RULES.has(v.rule));
	if (fatal.length && !i_know_what_im_doing) {
		throw new IncoherentIdentity(
			"Camouwolf refuses to launch an identity that contradicts itself:\n  - " +
				fatal.map((v) => v.detail).join("\n  - ") +
				"\nFix `config`, or pass i_know_what_im_doing=True to launch anyway.",
		);
	}

	validateConfig(config, browserBinary);

	// ---- switches ----------------------------------------------------------
	if (block_images) {
		LeakWarning.warn(loadWarnings().block_images, i_know_what_im_doing);
		args.push("--blink-settings=imagesEnabled=false");
	}
	if (headless === true) {
		args.push(headlessPointerSettings(config));
	}
	// A proxied context's WebRTC already leaves through the proxy (patch 0031);
	// this keeps it to TCP through the proxy, with no UDP at all.
	if (block_webrtc) {
		args.push("--force-webrtc-ip-handling-policy=disable_non_proxied_udp");
	}
	if (block_webgl) {
		LeakWarning.warn(loadWarnings().block_webgl, i_know_what_im_doing);
		args.push("--disable-webgl", "--disable-webgl2");
	}
	if (!enable_cache) args.push("--disk-cache-size=1");

	utilsDeps.addDefaultAddons(addons, exclude_addons);
	if (addons.length) {
		utilsDeps.confirmPaths(addons);
		args.push(
			`--load-extension=${addons.map((p) => path.resolve(p)).join(",")}`,
		);
	}
	// A repeated --disable-features replaces Playwright's own list (Chromium
	// reads the last one), so that list is restated with the launcher's.
	const disabled = [
		...PLAYWRIGHT_DISABLED_FEATURES,
		...LAUNCHER_DISABLED_FEATURES,
	];
	// Chrome 137+ ignores --load-extension unless this feature is off.
	if (addons.length) disabled.push("DisableLoadExtensionCommandLineSwitch");
	args.push(`--disable-features=${disabled.join(",")}`);

	const audit = auditSwitches(args, !i_know_what_im_doing);
	let launchArgs = audit.args;
	if (debug) {
		for (const [arg, reason] of audit.dropped) {
			utilsDeps.print(`[camouwolf] dropped ${arg}: ${reason}`);
		}
	}

	// The switches the audit refuses from a caller are the ones the launcher
	// derives from the identity, so the window, the UI language and the
	// Accept-Language header cannot disagree with it.
	// Chrome rounds the window through device pixels, so the switches carry the
	// input it reads back as the claimed window.
	const dpr = num(config["window.devicePixelRatio"] || 1);
	const outerWidth = config["window.outerWidth"];
	const outerHeight = config["window.outerHeight"];
	const screenX = config["window.screenX"];
	const screenY = config["window.screenY"];
	const hasPosition =
		screenX !== null &&
		screenX !== undefined &&
		screenY !== null &&
		screenY !== undefined;
	if (pyTruthy(outerWidth) && pyTruthy(outerHeight)) {
		const [x, width] = windowSwitchBounds(
			Math.trunc(num(screenX || 0)),
			Math.trunc(num(outerWidth)),
			dpr,
		);
		const [y, height] = windowSwitchBounds(
			Math.trunc(num(screenY || 0)),
			Math.trunc(num(outerHeight)),
			dpr,
		);
		launchArgs.push(`--window-size=${width},${height}`);
		if (hasPosition) launchArgs.push(`--window-position=${x},${y}`);
	} else if (hasPosition) {
		launchArgs.push(
			`--window-position=${Math.trunc(num(screenX))},${Math.trunc(num(screenY))}`,
		);
	}
	if (languages && pyTruthy(languages)) {
		launchArgs.push(`--lang=${languages[0]}`);
		launchArgs.push(`--accept-lang=${languages.join(",")}`);
		if (utilsDeps.osName() === "lin") {
			// Chromium on Linux takes its UI locale from the environment.
			env.LANGUAGE = languages[0].replaceAll("-", "_");
		}
	}
	launchArgs = mergeListSwitches(launchArgs);

	// ---- chromium_prefs ----------------------------------------------------
	if (pyTruthy(chromium_prefs)) {
		const userDataDir = passthrough.user_data_dir ?? passthrough.userDataDir;
		if (!userDataDir) {
			throw new ValueError(
				"chromium_prefs are written to the profile, so they need a persistent context: pass " +
					"persistent_context=True and user_data_dir=. For a non-persistent launch, use `args` " +
					"(command-line switches) instead.",
			);
		}
		writeChromiumPrefs(String(userDataDir), chromium_prefs as Config);
	}

	// ---- environment -------------------------------------------------------
	if (ownedDisplay) {
		const dpr = num(config["window.devicePixelRatio"] || 1);
		const width = config["screen.width"] || config["window.outerWidth"];
		const height = config["screen.height"] || config["window.outerHeight"];
		if (width && height)
			ownedDisplay.fit(Math.ceil(width * dpr), Math.ceil(height * dpr));
		env.DISPLAY = await ownedDisplay.get();
	}
	if (debug) {
		// Python pretty-prints with pprint(); the layout differs, the content
		// does not.
		utilsDeps.print("[DEBUG] Config:");
		utilsDeps.print(inspect(config, { depth: null, sorted: true }));
	}
	Object.assign(env, getEnvVars(config, targetOs, browserBinary));

	const ignored =
		passthrough.ignore_default_args ?? passthrough.ignoreDefaultArgs;
	delete passthrough.ignore_default_args;
	delete passthrough.ignoreDefaultArgs;
	const ignoreDefaultArgs: true | string[] =
		ignored === true
			? true
			: [
					...new Set<string>([
						...IGNORED_DEFAULT_ARGS,
						...(headless === true ? [PLAYWRIGHT_HEADLESS_BLINK_SETTINGS] : []),
						...(ignored || []),
					]),
				];

	return {
		executablePath: browserBinary,
		args: launchArgs,
		env,
		headless,
		ignoreDefaultArgs,
		...(pyTruthy(proxy) ? { proxy } : {}),
		...passthrough,
	};
}
