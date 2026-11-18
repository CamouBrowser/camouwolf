/**
 * `NewContext` must not hand back a context that contradicts itself.
 *
 * None of the seventeen per-context setters exists in the browser yet, so
 * `buildInitScript`'s calls are all typeof-guarded no-ops. What genuinely moves
 * per context is whatever Playwright carries — and that subset is a trap, not a
 * partial win: moving the UA alone leaves Sec-CH-UA-Platform and
 * navigator.platform describing the LAUNCH identity, which is precisely the
 * desync clienthints.ts exists to prevent, on a path where assertCoherent never
 * runs.
 */
import { describe, expect, it } from "vitest";

import {
	getCamouwolfLaunchConfig,
	incoherentContextKeys,
	setCamouwolfLaunchConfig,
} from "../../src/utils.js";

const WINDOWS = {
	"navigator.platform": "Win32",
	"navigator.userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152",
	"userAgentData.platform": "Windows",
	"navigator.hardwareConcurrency": 8,
	timezone: "Europe/Berlin",
	"screen.width": 1920,
};

describe("launch config tagging", () => {
	it("round-trips through the Browser object", () => {
		const browser = {};
		setCamouwolfLaunchConfig(browser, WINDOWS);
		expect(getCamouwolfLaunchConfig(browser)).toEqual(WINDOWS);
	});

	it("returns undefined for a browser Camouwolf did not launch", () => {
		expect(getCamouwolfLaunchConfig({})).toBeUndefined();
		expect(getCamouwolfLaunchConfig(null)).toBeUndefined();
		expect(getCamouwolfLaunchConfig(undefined)).toBeUndefined();
	});
});

describe("incoherentContextKeys", () => {
	it("flags a platform move", () => {
		expect(
			incoherentContextKeys(WINDOWS, {
				...WINDOWS,
				"navigator.platform": "MacIntel",
			}),
		).toEqual(["navigator.platform"]);
	});

	it("flags every moved surface, not just the first", () => {
		const moved = incoherentContextKeys(WINDOWS, {
			...WINDOWS,
			"navigator.platform": "MacIntel",
			"userAgentData.platform": "macOS",
			"navigator.hardwareConcurrency": 4,
		});
		expect(moved).toEqual([
			"navigator.platform",
			"navigator.hardwareConcurrency",
			"userAgentData.platform",
		]);
	});

	it("allows timezone to move — Chromium carries it into workers", () => {
		expect(
			incoherentContextKeys(WINDOWS, {
				...WINDOWS,
				timezone: "America/New_York",
			}),
		).toEqual([]);
	});

	it("allows an identical identity", () => {
		expect(incoherentContextKeys(WINDOWS, { ...WINDOWS })).toEqual([]);
	});

	it("compares font lists structurally, not by reference", () => {
		const launch = { ...WINDOWS, fonts: ["Arial", "Segoe UI"] };
		expect(
			incoherentContextKeys(launch, {
				...launch,
				fonts: ["Arial", "Segoe UI"],
			}),
		).toEqual([]);
		expect(
			incoherentContextKeys(launch, { ...launch, fonts: ["Arial"] }),
		).toEqual(["fonts"]);
	});

	it("ignores a key the context does not set at all", () => {
		// A context that says nothing about a surface inherits the launch's, which
		// is coherent by construction.
		const { "navigator.platform": _dropped, ...partial } = WINDOWS;
		expect(incoherentContextKeys(WINDOWS, partial)).toEqual([]);
	});

	it("is a no-op for a browser with no stashed launch config", () => {
		// A caller who built the Browser themselves gets no guard rather than a
		// spurious refusal.
		expect(
			incoherentContextKeys(undefined, {
				"navigator.platform": "MacIntel",
			}),
		).toEqual([]);
	});
});
