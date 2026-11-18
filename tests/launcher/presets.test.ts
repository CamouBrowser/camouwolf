/**
 * Audit of the shipped real-fingerprint pool.
 *
 * The pool is generated ahead of time precisely so it can be checked once
 * rather than repaired at every launch. These tests are that check: every
 * preset in `src/data-files/fingerprint-presets.json` must describe a machine
 * that could exist. A single incoherent entry is worse than a smaller pool,
 * because it is drawn silently and only fails at the target.
 */
import { describe, expect, it } from "vitest";

import { CONSTRAINTS } from "../../src/__version__.js";
import { assertCoherent } from "../../src/clienthints.js";
import {
	type FingerprintPreset,
	fromPreset,
	getRandomPreset,
	loadPresets,
	presetOsKey,
} from "../../src/presets.js";
import { backendMatchesOs } from "../../src/webgl/sample.js";

const bundle = loadPresets();
const OS_KEYS = ["windows", "macos", "linux"] as const;

/** Every preset, tagged with the pool it came from. */
function all(): { os: (typeof OS_KEYS)[number]; preset: FingerprintPreset }[] {
	const out: { os: (typeof OS_KEYS)[number]; preset: FingerprintPreset }[] = [];
	for (const os of OS_KEYS) {
		for (const preset of bundle?.presets[os] ?? []) out.push({ os, preset });
	}
	return out;
}

describe("preset bundle", () => {
	it("exists and is pinned to the shipped Chromium", () => {
		expect(bundle).toBeTruthy();
		// A pool generated against an older milestone would put every preset's UA
		// and brand list out of step with the binary.
		expect(bundle?.chromium).toBe(CONSTRAINTS.CHROMIUM_VERSION);
		expect(bundle?.milestone).toBe(CONSTRAINTS.CHROMIUM_MILESTONE);
	});

	it("carries a usable pool for every OS", () => {
		for (const os of OS_KEYS) {
			expect(bundle?.presets[os]?.length ?? 0).toBeGreaterThanOrEqual(50);
		}
	});

	it("has no duplicate identities", () => {
		// Duplicates make the pool look larger than it is, which matters: two
		// launches drawing the same identity are linkable to each other.
		for (const os of OS_KEYS) {
			const keys = (bundle?.presets[os] ?? []).map((p) =>
				[
					p.navigator?.hardwareConcurrency,
					p.navigator?.deviceMemory,
					p.screen?.width,
					p.screen?.height,
					p.screen?.availHeight,
					p.screen?.devicePixelRatio,
					p.webgl?.unmaskedRenderer,
					p.userAgentData?.platformVersion,
				].join("|"),
			);
			expect(new Set(keys).size).toBe(keys.length);
		}
	});
});

describe("every preset", () => {
	const presets = all();

	it("is internally coherent once converted to config", () => {
		const bad: string[] = [];
		for (const { preset } of presets) {
			const problems = assertCoherent(fromPreset(preset, bundle?.milestone));
			if (problems.length) {
				bad.push(`${preset.navigator?.userAgent}: ${problems.join("; ")}`);
			}
		}
		expect(bad).toEqual([]);
	});

	it("claims a GPU whose ANGLE backend matches its OS", () => {
		const bad: string[] = [];
		for (const { preset } of presets) {
			const renderer = preset.webgl?.unmaskedRenderer ?? "";
			if (!backendMatchesOs(renderer, presetOsKey(preset))) {
				bad.push(`${presetOsKey(preset)}: ${renderer}`);
			}
		}
		expect(bad).toEqual([]);
	});

	it("never claims a software adapter", () => {
		// "Microsoft Basic Render Driver" / SwiftShader / llvmpipe all mean "a VM
		// with no GPU", which is the opposite of what a residential identity
		// should report.
		const bad = presets
			.map(({ preset }) => preset.webgl?.unmaskedRenderer ?? "")
			.filter((r) => /SwiftShader|llvmpipe|Basic Render Driver/i.test(r));
		expect(bad).toEqual([]);
	});

	it("reports a device memory on the spec ladder", () => {
		// navigator.deviceMemory is quantized to {0.25,0.5,1,2,4,8}; an off-ladder
		// value is itself the tell, regardless of how plausible the number is.
		const ladder = new Set([0.25, 0.5, 1, 2, 4, 8]);
		const bad = presets
			.map(({ preset }) => preset.navigator?.deviceMemory)
			.filter((dm) => dm !== undefined && !ladder.has(dm));
		expect(bad).toEqual([]);
	});

	it("reports a consumer-desktop core count", () => {
		// Above 32 is a workstation or a server. A browser claiming 144 cores is
		// announcing a datacenter — the one thing a residential identity must not
		// do — so the generator rejects those outright.
		for (const { preset } of presets) {
			const hc = preset.navigator?.hardwareConcurrency ?? 0;
			expect(hc).toBeGreaterThanOrEqual(2);
			expect(hc).toBeLessThanOrEqual(32);
			expect(Number.isInteger(hc)).toBe(true);
		}
	});

	it("keeps a taskbar inset", () => {
		for (const { preset } of presets) {
			const s = preset.screen ?? {};
			const flush = s.availWidth === s.width && s.availHeight === s.height;
			expect(flush).toBe(false);
		}
	});

	it("pairs Apple Silicon GPUs with an arm architecture hint", () => {
		// A Mac claiming x86 with an M-series GPU (or the reverse) is a
		// contradiction inside one identity.
		for (const { preset } of presets) {
			if (presetOsKey(preset) !== "mac") continue;
			const apple = /Apple M\d/.test(preset.webgl?.unmaskedRenderer ?? "");
			expect(preset.userAgentData?.architecture).toBe(apple ? "arm" : "x86");
		}
	});
});

describe("fromPreset", () => {
	it("fills fonts and voices from the manifest, not the preset", () => {
		// The manifest draw is filtered to what the build can render; a preset's
		// own font list describes the donor machine, not ours.
		const preset = getRandomPreset("windows", () => 0.5);
		expect(preset).toBeTruthy();
		const config = fromPreset(preset as FingerprintPreset, 152);
		expect((config.fonts as string[]).length).toBeGreaterThan(0);
		expect((config.voices as unknown[]).length).toBeGreaterThan(0);
	});

	it("is reproducible for a fixed rng", () => {
		const preset = getRandomPreset("windows", () => 0.25) as FingerprintPreset;
		const rngA = () => 0.42;
		const rngB = () => 0.42;
		expect(fromPreset(preset, 152, { rng: rngA })).toEqual(
			fromPreset(preset, 152, { rng: rngB }),
		);
	});

	it("retargets the UA onto the requested milestone", () => {
		const preset = getRandomPreset("windows", () => 0.1) as FingerprintPreset;
		const config = fromPreset(preset, 160);
		expect(config["navigator.userAgent"]).toContain("Chrome/160.0.0.0");
	});
});
