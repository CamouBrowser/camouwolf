/**
 * The farbling posture: every perturbation surface is off unless explicitly
 * armed, and no single value can arm more than one of them.
 *
 * This is the regression these tests exist for. `canvas:seed` used to gate
 * canvas readback perturbation, OfflineAudioContext/AnalyserNode sample
 * perturbation AND TextMetrics quantization all at once, so one number turned
 * on three unrelated tells. The seed is now only a delta source; arming is a
 * separate, per-surface decision.
 *
 * Why the default matters rather than merely being tidy: unlike Firefox, stock
 * Chrome randomizes nothing — not even the encode path Holo had to preserve —
 * so any perturbation here is un-Chrome-like with no native behaviour to hide
 * behind. See the header of browser/additions/camouwolfcfg/camouwolf_farble.h.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
	type FingerprintPreset,
	fromPreset,
	getRandomPreset,
} from "../../src/presets.js";
import {
	applyFarblingDefaults,
	type ConfigMap,
	FARBLE_OPT_INS,
	launchOptions,
} from "../../src/utils.js";

const REPO = path.resolve(
	path.dirname(fileURLToPath(import.meta.url)),
	"../..",
);

/** Reassemble the config from the chunked CAMOU_CONFIG_* environment. */
function configFrom(built: Record<string, unknown>): ConfigMap {
	const env = built.env as Record<string, string>;
	const json = Object.keys(env)
		.filter((k) => k.startsWith("CAMOU_CONFIG_"))
		.sort((a, b) => Number(a.split("_").pop()) - Number(b.split("_").pop()))
		.map((k) => env[k])
		.join("");
	return JSON.parse(json);
}

async function build(over: Record<string, unknown> = {}) {
	return launchOptions({
		headless: true,
		stealth: true,
		seed: 1234,
		executable_path: "/nonexistent/camouwolf",
		...over,
	});
}

describe("farbling defaults", () => {
	it("leaves every opt-in off on a generated launch identity", async () => {
		// linux -> macos is refused by design (UnsupportedOSSpoof), so the two
		// targets this host can actually build are the two worth asserting on.
		for (const os of ["windows", "linux"] as const) {
			const config = configFrom(await build({ os }));
			for (const key of FARBLE_OPT_INS) {
				expect(config[key], `${os} armed ${key}`).toBe(false);
			}
		}
	});

	it("leaves every opt-in off on a preset identity", () => {
		const preset = getRandomPreset("windows", () => 0.5) as FingerprintPreset;
		const config = fromPreset(preset, 152);
		for (const key of FARBLE_OPT_INS) {
			expect(config[key], `preset armed ${key}`).toBe(false);
		}
	});

	it("does not arm anything from canvas:seed alone", async () => {
		// The exact shape of the old bug: a nonzero seed used to be the whole
		// switch. It must now be inert on its own.
		const config = configFrom(
			await build({ os: "windows", config: { "canvas:seed": 12345 } }),
		);
		expect(config["canvas:seed"]).toBe(12345);
		for (const key of FARBLE_OPT_INS) {
			expect(config[key], `canvas:seed armed ${key}`).toBe(false);
		}
	});

	it("keeps an explicit opt-in, so the old posture stays measurable", () => {
		// Holo pref-gated its readback noise rather than deleting it, so the
		// posture it removed could still be measured without a rebuild. Same here.
		const config: ConfigMap = { "canvas:randomizeReadback": true };
		applyFarblingDefaults(config);
		expect(config["canvas:randomizeReadback"]).toBe(true);
		expect(config["AudioContext:randomizeSamples"]).toBe(false);
		expect(config["canvas:quantizeTextMetrics"]).toBe(false);
	});

	it("arms each surface independently", () => {
		// One key must never imply another: that independence is the whole point
		// of splitting them out of the seed.
		for (const armed of FARBLE_OPT_INS) {
			const config: ConfigMap = { [armed]: true };
			applyFarblingDefaults(config);
			for (const key of FARBLE_OPT_INS) {
				expect(config[key], `${armed} leaked into ${key}`).toBe(key === armed);
			}
		}
	});
});

describe("farbling opt-ins stay wired to the browser", () => {
	// scripts/check-config-keys.py joins the launcher and the browser by
	// grepping both sides for literal key strings. These assertions are the
	// same join, expressed where a `pnpm test` run will see it: if either side
	// stops spelling a key out, the C++ reader can rot away silently.
	const farbleHeader = fs.readFileSync(
		path.join(REPO, "browser/additions/camouwolfcfg/camouwolf_farble.h"),
		"utf8",
	);
	const utilsSrc = fs.readFileSync(path.join(REPO, "src/utils.ts"), "utf8");

	for (const key of FARBLE_OPT_INS) {
		it(`${key} is read by camouwolf_farble.h`, () => {
			expect(farbleHeader).toContain(`GetBool("${key}")`);
		});

		it(`${key} is written as a literal by applyFarblingDefaults`, () => {
			expect(utilsSrc).toContain(`config["${key}"] ??= false;`);
		});
	}

	it("has no opt-in the header reads but the launcher never writes", () => {
		const readByHeader = [
			...farbleHeader.matchAll(/GetBool\("([^"]+)"\)/g),
		].map((m) => m[1]);
		expect(new Set(readByHeader)).toEqual(new Set(FARBLE_OPT_INS));
	});
});
