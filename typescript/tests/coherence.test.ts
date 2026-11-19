/**
 * The identity is checked against itself on every launch.
 * Twin of python/tests/test_coherence.py.
 */
import { describe, expect, it } from "vitest";
import * as clienthints from "../src/clienthints.js";
import * as coherence from "../src/coherence.js";

function windows(): Record<string, any> {
	return {
		...clienthints.clientHintConfig({
			milestone: 152,
			fullVersion: "152.0.7977.42",
			os: "win",
		}),
		"navigator.hardwareConcurrency": 8,
		"navigator.deviceMemory": 8,
		"screen.width": 1920,
		"screen.height": 1080,
		"screen.availWidth": 1920,
		"screen.availHeight": 1040,
		"screen.availLeft": 0,
		"screen.availTop": 0,
		"screen.colorDepth": 24,
		"window.outerWidth": 1600,
		"window.outerHeight": 900,
		"window.screenX": 0,
		"window.screenY": 0,
		"webGl:vendor": "Google Inc. (NVIDIA)",
		"webGl:renderer":
			"ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)",
	};
}

const rules = (violations: coherence.Violation[]) =>
	new Set(violations.map((v) => v.rule));

describe("coherence", () => {
	it("finds no violation in a coherent identity", () => {
		expect(coherence.validate(windows(), "win")).toEqual([]);
		expect(coherence.apply(windows(), "win")).toEqual([]);
	});

	it("makes client hints that contradict the user agent fatal", () => {
		const config = { ...windows(), "userAgentData.platform": "macOS" };
		expect(rules(coherence.validate(config, "win"))).toContain("client-hints");
		expect(coherence.FATAL_RULES).toContain("client-hints");
	});

	it("makes a GPU on a backend the OS never uses fatal", () => {
		const config = {
			...windows(),
			"webGl:renderer":
				"ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
		};
		const violations = coherence.apply(config, "win");
		expect(rules(violations)).toContain("gpu-matches-os");
		expect(coherence.FATAL_RULES).toContain("gpu-matches-os");
		// Not repaired: there is no right GPU to invent.
		expect(config["webGl:renderer"]).toContain("Metal");
	});

	it("repairs repairable values the launch drew", () => {
		const config = {
			...windows(),
			"screen.colorDepth": 32,
			"navigator.deviceMemory": 6,
			"screen.availHeight": 1200,
			"window.outerWidth": 2400,
		};
		const violations = coherence.apply(config, "win");
		expect(violations.filter((v) => v.kept.length)).toEqual([]);
		expect(config["screen.colorDepth"]).toBe(24);
		expect([0.25, 0.5, 1, 2, 4, 8]).toContain(
			Number(config["navigator.deviceMemory"]),
		);
		expect(config["screen.availHeight"]).toBeLessThanOrEqual(1080);
		expect(config["window.outerWidth"]).toBeLessThanOrEqual(1920);
		expect(coherence.validate(config, "win")).toEqual([]);
	});

	it("keeps and reports a caller's value", () => {
		const config = { ...windows(), "screen.colorDepth": 32 };
		const violations = coherence.apply(
			config,
			"win",
			new Set(["screen.colorDepth"]),
		);
		expect(config["screen.colorDepth"]).toBe(32);
		expect(
			violations.filter((v) => v.rule === "color-depth").map((v) => v.kept),
		).toEqual([["screen.colorDepth"]]);
	});

	it("turns a portrait desktop screen", () => {
		const config = {
			...windows(),
			"screen.width": 1080,
			"screen.height": 1920,
			"screen.availWidth": 1080,
			"screen.availHeight": 1880,
		};
		expect(coherence.screenIsImplausible(config)).toBe(true);
		expect(coherence.repairScreenOrientation(config)).toBe(true);
		expect([config["screen.width"], config["screen.height"]]).toEqual([
			1920, 1080,
		]);
	});

	it("turns a zoomed ratio into the display behind it", () => {
		// A scraped 1760x990 at 1.0909 is a 1920x1080 monitor at 100% with the
		// page zoomed to 110%: no Windows display setting offers 1.0909.
		const config: Record<string, any> = {
			...windows(),
			"window.devicePixelRatio": 1.0909,
			"screen.width": 1760,
			"screen.height": 990,
			"screen.availWidth": 1760,
			"screen.availHeight": 953,
			"window.outerWidth": 1466,
			"window.outerHeight": 825,
		};
		expect(coherence.validate(config, "win").map((v) => v.rule)).toContain(
			"device-pixel-ratio",
		);
		coherence.repairDevicePixelRatio(config, "win");
		expect(config["window.devicePixelRatio"]).toBe(1);
		expect([config["screen.width"], config["screen.height"]]).toEqual([
			1920, 1080,
		]);
		expect(config["window.outerWidth"]).toBe(1599);
		expect(coherence.validate(config, "win")).toEqual([]);
	});

	it("leaves a display step alone", () => {
		const config = { ...windows(), "window.devicePixelRatio": 1.25 };
		coherence.repairDevicePixelRatio(config, "win");
		expect(config).toEqual({ ...windows(), "window.devicePixelRatio": 1.25 });
		// macOS offers 1 and 2 only.
		expect(coherence.isZoomedRatio(1.25, "mac")).toBe(true);
		expect(coherence.isZoomedRatio(2, "mac")).toBe(false);
	});

	it("keeps and reports a caller's ratio", () => {
		const config = { ...windows(), "window.devicePixelRatio": 1.0909 };
		const violations = coherence.apply(
			config,
			"win",
			new Set(["window.devicePixelRatio"]),
		);
		expect(config["window.devicePixelRatio"]).toBe(1.0909);
		expect(config["screen.width"]).toBe(1920);
		expect(
			violations
				.filter((v) => v.rule === "device-pixel-ratio")
				.map((v) => v.kept),
		).toEqual([["window.devicePixelRatio"]]);
	});
});
