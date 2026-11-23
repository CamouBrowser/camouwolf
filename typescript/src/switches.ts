/**
 * Chromium command-line hygiene.
 *
 * Camoufox has no counterpart: Firefox's automation surface is Juggler,
 * compiled in. Chromium's is the command line, which holds three kinds of
 * problem:
 *
 * 1. Switches that mark the process as automated (`--enable-automation`, which
 *    Playwright adds to every launch).
 * 2. Switches that change behaviour a page can measure (`--disable-web-security`).
 * 3. Switches whose value contradicts the identity. `--user-agent` moves
 *    navigator.userAgent without the client hints (see clienthints.ts), so it
 *    is refused and the caller pointed at `config`.
 *
 * The identity itself travels in the environment (CAMOU_CONFIG_*), never in
 * argv: argv is readable in /proc/<pid>/cmdline and `ps`, and Chromium copies
 * much of it to its renderers' command lines.
 *
 * TypeScript twin of python/src/camouwolf/switches.py (itself ported from
 * Kraft's src/switches.ts; where the two differ, this follows the Python).
 */
import { ValueError } from "./exceptions.js";

/**
 * Switches Playwright and Puppeteer add that mark the browser as automated.
 * Dropped from the caller's args.
 */
export const AUTOMATION_SWITCHES: ReadonlySet<string> = new Set([
	"--enable-automation",
	"--enable-blink-features=IdleDetection",
	"--remote-debugging-pipe-log",
	"--test-type",
	"--no-service-autorun",
	"--password-store=basic",
	"--use-mock-keychain",
]);

/**
 * Playwright's own default arguments that are left out of the launch
 * (`ignoreDefaultArgs`). --enable-automation drives the "controlled by
 * automated test software" infobar and upstream's navigator.webdriver;
 * --disable-extensions would stop `addons` from loading; and
 * --disable-popup-blocking lets a page open a window without a user gesture,
 * which stock Chrome blocks and a page can test.
 */
export const IGNORED_DEFAULT_ARGS: readonly string[] = [
	"--enable-automation",
	"--disable-extensions",
	"--disable-popup-blocking",
];

/**
 * What Playwright's headless launch tells Blink about the pointer, since a
 * headless browser has no input device: a mouse and nothing else. The launcher
 * states it in its place (headlessPointerSettings), with the touchscreen a
 * touch identity has beside it.
 */
export const PLAYWRIGHT_HEADLESS_BLINK_SETTINGS =
	"--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4";

/**
 * Playwright's headless pointer, plus the coarse pointer of the touchscreen a
 * touch identity has beside its mouse (ui::PointerType: 2 coarse, 4 fine;
 * ui::HoverType: 2 hover).
 */
export function headlessPointerSettings(
	config: Record<string, unknown>,
): string {
	const pointers = 4 | (config["navigator.maxTouchPoints"] ? 2 : 0);
	// Pointer first: Playwright drops every argument equal to one in
	// ignoreDefaultArgs, ours included, so this must never spell its default.
	return `--blink-settings=primaryPointerType=4,availablePointerTypes=${pointers},primaryHoverType=2,availableHoverTypes=2`;
}

/**
 * The features Playwright's Chromium launch disables (playwright-core
 * chromiumSwitches.ts, 1.59-1.62). Chromium reads only the last
 * --disable-features on a command line, so when the launcher needs one of its
 * own (extensions), it restates this list with it rather than silently turning
 * Playwright's back on.
 */
export const PLAYWRIGHT_DISABLED_FEATURES: readonly string[] = [
	"AvoidUnnecessaryBeforeUnloadCheckSync",
	"BoundaryEventDispatchTracksNodeRemoval",
	"DestroyProfileOnBrowserClose",
	"DialMediaRouteProvider",
	"GlobalMediaControls",
	"HttpsUpgrades",
	"LensOverlay",
	"MediaRouter",
	"PaintHolding",
	"ThirdPartyStoragePartitioning",
	"BlockOriginHeaderModificationOnRedirect",
	"Translate",
	"AutoDeElevate",
	"OptimizationHints",
	"msForceBrowserSignIn",
	"msEdgeUpdateLaunchServicesPreferredVersion",
];

/**
 * Disabled by Playwright after 1.62 (microsoft/playwright#43877). Chromium 158
 * auto-enables HTTPS-First balanced mode, which upgrades an http:// navigation
 * and fails it with ERR_BLOCKED_BY_CLIENT when the upgrade does not connect.
 * The launcher restates the list with these on every launch.
 */
export const LAUNCHER_DISABLED_FEATURES: readonly string[] = [
	"HttpsFirstBalancedModeAutoEnable",
];

/**
 * Switches that change web-visible behaviour: cross-origin reads without CORS,
 * no site isolation, and so on. A page can test for each directly.
 */
export const BEHAVIOR_CHANGING_SWITCHES: ReadonlySet<string> = new Set([
	"--disable-web-security",
	"--disable-site-isolation-trials",
	"--disable-features=IsolateOrigins,site-per-process",
	"--allow-running-insecure-content",
	"--disable-popup-blocking",
	"--ignore-certificate-errors",
]);

/**
 * Switches whose value would contradict the CAMOU_CONFIG identity, with the
 * option to use instead. Refused rather than dropped: a caller passing
 * --user-agent believes the user agent is set.
 */
export const CONFLICTING_SWITCHES: Readonly<Record<string, string>> = {
	"--user-agent":
		"Setting --user-agent moves navigator.userAgent but not the Sec-CH-UA-* client hints, " +
		"so the two disagree on every request. Pass the identity through `config` or " +
		"`fingerprint` instead; Camouwolf derives the user agent and the hints from it together.",
	"--lang":
		"Setting --lang moves the UI language but not navigator.languages or the " +
		"Accept-Language header. Use the `locale` option, which sets all three.",
	"--accept-lang":
		"Setting --accept-lang moves the Accept-Language header but not the UI language or " +
		"navigator.languages. Use the `locale` option, which sets all three.",
	"--window-size":
		"Setting --window-size moves the real window without the screen the identity claims. " +
		"Use the `window` option.",
	"--window-position":
		"Setting --window-position moves the real window without the screen the identity " +
		"claims. Set window.screenX and window.screenY in `config`.",
	"--force-device-scale-factor":
		"Setting the device scale factor here separates it from the identity's screen. " +
		"Use the `screen` option.",
	"--proxy-server":
		"Use the `proxy` option, which also sends the geoip lookup through the proxy.",
	"--headless":
		"Use the `headless` option. The switch bypasses headless='virtual' and 'virtual-gpu'.",
	"--user-data-dir": "Use `persistent_context=True` with `user_data_dir`.",
};

/**
 * Switches every Camouwolf launch adds. Each removes something a fresh
 * automated profile shows and a person's does not, or a request to Google a
 * datacenter launch should not make. Nothing here changes rendering or what a
 * page can do.
 */
export const BASE_SWITCHES: readonly string[] = [
	"--no-first-run",
	"--no-default-browser-check",
	"--disable-background-networking",
	"--disable-component-update",
	"--disable-domain-reliability",
	"--metrics-recording-only",
	"--disable-breakpad",
	"--disable-blink-features=AutomationControlled",
];

/**
 * Switches whose value is a comma-separated list. Chromium reads only the last
 * occurrence of a repeated switch, so several are merged into one.
 */
export const LIST_SWITCHES: ReadonlySet<string> = new Set([
	"--enable-features",
	"--disable-features",
	"--enable-blink-features",
	"--disable-blink-features",
	"--load-extension",
	"--blink-settings",
]);

export interface SwitchAudit {
	/** The arguments to launch with. */
	args: string[];
	/** [argument, reason] for each one dropped. */
	dropped: Array<[string, string]>;
}

/** str.split('=', 1): the part before the first `=`, and the rest (or undefined). */
function splitOnce(arg: string): [string, string | undefined] {
	const at = arg.indexOf("=");
	return at === -1 ? [arg, undefined] : [arg.slice(0, at), arg.slice(at + 1)];
}

export function flagName(arg: string): string {
	return splitOnce(arg)[0];
}

/**
 * Merge repeated list-valued switches (--disable-features=A and
 * --disable-features=B) into one, at the position of the first.
 */
export function mergeListSwitches(args: Iterable<string>): string[] {
	const merged: string[] = [];
	const position = new Map<string, number>();
	for (const arg of args) {
		const [name, value] = splitOnce(arg);
		if (!LIST_SWITCHES.has(name) || value === undefined) {
			merged.push(arg);
			continue;
		}
		const values = value.split(",").filter((v) => v);
		const at = position.get(name);
		if (at !== undefined) {
			const current = (splitOnce(merged[at])[1] as string).split(",");
			merged[at] =
				`${name}=${[...current, ...values.filter((v) => !current.includes(v))].join(",")}`;
		} else {
			position.set(name, merged.length);
			merged.push(`${name}=${[...new Set(values)].join(",")}`);
		}
	}
	return merged;
}

/**
 * Filter the caller's arguments and add the base switches.
 *
 * `strict` throws ValueError on a switch that conflicts with the identity
 * instead of dropping it.
 */
export function auditSwitches(
	args: Iterable<string>,
	strict = true,
): SwitchAudit {
	const dropped: Array<[string, string]> = [];
	const kept: string[] = [];

	for (const arg of args) {
		const name = flagName(arg);
		const conflict = Object.hasOwn(CONFLICTING_SWITCHES, name)
			? CONFLICTING_SWITCHES[name]
			: undefined;
		if (conflict) {
			if (strict) {
				throw new ValueError(`Camouwolf refuses '${name}'. ${conflict}`);
			}
			dropped.push([arg, conflict]);
		} else if (AUTOMATION_SWITCHES.has(arg) || AUTOMATION_SWITCHES.has(name)) {
			dropped.push([arg, "automation marker"]);
		} else if (
			BEHAVIOR_CHANGING_SWITCHES.has(arg) ||
			BEHAVIOR_CHANGING_SWITCHES.has(name)
		) {
			dropped.push([arg, "changes web-visible behavior"]);
		} else {
			kept.push(arg);
		}
	}

	// Base switches first; a caller's list-valued switch of the same name is
	// merged with it, and any other repeat is the caller's (Chromium reads the last).
	const seen = new Set(
		kept.map(flagName).filter((name) => !LIST_SWITCHES.has(name)),
	);
	const base = BASE_SWITCHES.filter((s) => !seen.has(flagName(s)));
	return { args: mergeListSwitches([...base, ...kept]), dropped };
}
