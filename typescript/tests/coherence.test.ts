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
});
