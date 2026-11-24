/**
 * User-Agent Client Hints: the surface Firefox does not have.
 *
 * A Chrome identity is a set of values that must agree:
 *
 *     navigator.userAgent            "...Windows NT 10.0; Win64; x64...Chrome/158.0.0.0..."
 *     navigator.userAgentData.brands [{Chromium 158}, {GREASE}], built by Chromium itself
 *     Sec-CH-UA                      the same brand list, on every request
 *     Sec-CH-UA-Platform             "Windows"
 *     Sec-CH-UA-Mobile               ?0
 *     Sec-CH-UA-Arch / -Bitness / -Platform-Version / -Full-Version-List (high entropy)
 *
 * The low-entropy hints go out on every request with no opt-in, so a server
 * sees them before any script runs. `--user-agent` moves the UA string and not
 * the hints, which is why switches.ts refuses it and everything here is derived
 * from one identity. The keys are read by
 * browser/patches/0003-user-agent-client-hints.patch.
 *
 * TypeScript twin of python/src/camouwolf/clienthints.py.
 */
import { pyStr, pyTruthy } from "./pycompat.js";

export type OsKey = "win" | "mac" | "lin";

/** Sec-CH-UA-Platform values. */
export const CH_PLATFORM: Readonly<Record<OsKey, string>> = {
	win: "Windows",
	mac: "macOS",
	lin: "Linux",
};

/** navigator.platform values, which are not the Sec-CH-UA-Platform ones. */
export const NAV_PLATFORM: Readonly<Record<OsKey, string>> = {
	win: "Win32",
	mac: "MacIntel",
	lin: "Linux x86_64",
};

/**
 * The parenthesised part of the UA string. Frozen by Chrome's UA reduction
 * since M100: macOS stays "10_15_7", Windows "10.0; Win64; x64" from 10 through
 * 11, Linux "X11; Linux x86_64". The real OS version is in
 * Sec-CH-UA-Platform-Version.
 */
export const UA_PLATFORM_TOKEN: Readonly<Record<OsKey, string>> = {
	win: "Windows NT 10.0; Win64; x64",
	mac: "Macintosh; Intel Mac OS X 10_15_7",
	lin: "X11; Linux x86_64",
};

/**
 * Sec-CH-UA-Platform-Version values Chrome reports, per OS. assertCoherent
 * refuses any other.
 *   win: the UniversalApiContract ladder, not the marketing version: every
 *        Windows 10 reports 10.0.0, Windows 11 21H2/22H2 13.0.0/14.0.0/15.0.0,
 *        and 19.0.0 was measured on build 26200. 16-18 have not been seen on a
 *        shipping build, so they are absent.
 *   mac: macOS Sonoma and Sequoia point releases.
 *   lin: the kernel version.
 */
export const PLATFORM_VERSIONS: Readonly<Record<OsKey, readonly string[]>> = {
	win: ["10.0.0", "13.0.0", "14.0.0", "15.0.0", "19.0.0"],
	mac: ["14.5.0", "14.6.1", "15.1.0", "15.2.0", "15.3.1"],
	lin: ["6.8.0", "6.11.0", "6.12.0"],
};

/** The version a generated identity claims when nothing names one. */
export const DEFAULT_PLATFORM_VERSION: Readonly<Record<OsKey, string>> = {
	win: "15.0.0",
	mac: "15.3.1",
	lin: "6.12.0",
};

const CHROME_VERSION = /Chrome\/\d+(\.\d+)*/g;

export interface ChromeIdentity {
	milestone: number;
	fullVersion: string;
	os: OsKey;
	platformVersion?: string | null;
	/** 'x86' | 'arm' */
	arch?: string | null;
	bitness?: string;
	model?: string;
	wow64?: boolean;
	mobile?: boolean;
}

/** The OS a user agent claims, or null. */
export function uaOs(userAgent: string): OsKey | null {
	if (userAgent.includes("Windows NT")) return "win";
	if (userAgent.includes("Mac OS X")) return "mac";
	if (/Linux|X11|CrOS/.test(userAgent)) return "lin";
	return null;
}

export function buildUserAgent(identity: ChromeIdentity): string {
	return (
		`Mozilla/5.0 (${UA_PLATFORM_TOKEN[identity.os]}) ` +
		"AppleWebKit/537.36 (KHTML, like Gecko) " +
		`Chrome/${identity.milestone}.0.0.0 Safari/537.36`
	);
}

/**
 * The user agent with its Chrome version set to `milestone`.0.0.0.
 *
 * fpgen's fingerprints were recorded on whatever Chrome the machine ran; a UA
 * behind the binary disagrees with the brand list the browser builds.
 */
export function retargetUserAgent(
	userAgent: string,
	milestone: number | string,
): string {
	return userAgent.replace(
		CHROME_VERSION,
		`Chrome/${Math.trunc(Number(milestone))}.0.0.0`,
	);
}

/** Every client-hint config key for one identity. */
export function clientHintConfig(
	identity: ChromeIdentity,
): Record<string, any> {
	return {
		"navigator.userAgent": buildUserAgent(identity),
		"navigator.platform": NAV_PLATFORM[identity.os],
		"userAgentData.platform": CH_PLATFORM[identity.os],
		"userAgentData.platformVersion":
			identity.platformVersion || DEFAULT_PLATFORM_VERSION[identity.os],
		"userAgentData.architecture":
			identity.arch || (identity.os === "mac" ? "arm" : "x86"),
		"userAgentData.bitness": identity.bitness ?? "64",
		// Desktop Chrome sends an empty model; a populated one means Android.
		"userAgentData.model": identity.model ?? "",
		"userAgentData.uaFullVersion": identity.fullVersion,
		// 32-bit Chrome on 64-bit Windows; false whenever bitness is 64.
		"userAgentData.wow64": identity.wow64 ?? false,
		"userAgentData.mobile": identity.mobile ?? false,
	};
}

/**
 * Set the client hints the identity's user agent implies, keeping any the
 * config already holds.
 */
export function fillClientHints(
	config: Record<string, any>,
	targetOs: OsKey,
	fullVersion: string,
): void {
	const identity: ChromeIdentity = {
		milestone: Number.parseInt(fullVersion.split(".", 1)[0], 10),
		fullVersion,
		os: targetOs,
	};
	for (const [key, value] of Object.entries(clientHintConfig(identity))) {
		if (key === "navigator.userAgent" || key === "navigator.platform") continue;
		if (!(key in config)) config[key] = value;
	}
}

/**
 * The ways this config's user agent and client hints contradict each other.
 *
 * Run on every launch: the hints are derived from one identity, but a caller
 * can set any of them through `config`. Empty means coherent.
 */
export function assertCoherent(config: Record<string, any>): string[] {
	const problems: string[] = [];
	const ua = config["navigator.userAgent"];
	if (!ua || typeof ua !== "string") return problems;
	const chPlatform = config["userAgentData.platform"];
	const navPlatform = config["navigator.platform"];
	const osKey = uaOs(ua);

	if (osKey && pyTruthy(chPlatform) && CH_PLATFORM[osKey] !== chPlatform) {
		problems.push(
			`navigator.userAgent claims ${CH_PLATFORM[osKey]} but Sec-CH-UA-Platform says ` +
				`"${pyStr(chPlatform)}"; every request carries this hint.`,
		);
	}
	if (osKey && pyTruthy(navPlatform) && NAV_PLATFORM[osKey] !== navPlatform) {
		problems.push(
			`navigator.userAgent claims ${CH_PLATFORM[osKey]} but navigator.platform is ` +
				`"${pyStr(navPlatform)}" (expected "${NAV_PLATFORM[osKey]}").`,
		);
	}

	const platformVersion = config["userAgentData.platformVersion"];
	if (
		osKey &&
		pyTruthy(platformVersion) &&
		!PLATFORM_VERSIONS[osKey].includes(platformVersion)
	) {
		problems.push(
			`Sec-CH-UA-Platform-Version "${pyStr(platformVersion)}" is not a value Chrome reports on ` +
				`${CH_PLATFORM[osKey]} (one of ${PLATFORM_VERSIONS[osKey].join(", ")}). On Windows ` +
				"this is the UA-CH ladder, not the marketing version.",
		);
	}

	if (config["userAgentData.mobile"] === true && !ua.includes("Mobile")) {
		problems.push(
			"Sec-CH-UA-Mobile is ?1 but the user agent has no 'Mobile' token.",
		);
	}

	if (
		config["userAgentData.wow64"] === true &&
		config["userAgentData.bitness"] === "64"
	) {
		problems.push(
			"Sec-CH-UA-WoW64 is ?1 with 64-bit bitness; WoW64 is a 32-bit Chrome on 64-bit " +
				'Windows, so bitness must be "32".',
		);
	}

	const model = config["userAgentData.model"];
	if (pyTruthy(model) && !/Android|Mobile/.test(ua)) {
		problems.push(
			`Sec-CH-UA-Model is "${pyStr(model)}" on a desktop user agent; desktop Chrome sends "".`,
		);
	}

	const fullVersion = config["userAgentData.uaFullVersion"];
	const uaMajor = /Chrome\/(\d+)/.exec(ua);
	if (
		pyTruthy(fullVersion) &&
		uaMajor &&
		pyStr(fullVersion).split(".", 1)[0] !== uaMajor[1]
	) {
		problems.push(
			`navigator.userAgent says Chrome/${uaMajor[1]} but ` +
				`Sec-CH-UA-Full-Version-List says ${pyStr(fullVersion)}.`,
		);
	}

	return problems;
}
