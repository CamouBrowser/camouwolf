/**
 * Invariants the launcher must hold regardless of which identity it drew.
 *
 * Each of these encodes a specific way a launch can look wrong. They run
 * against `launchOptions()` itself — the real code path — rather than against
 * a hand-built config, because the point is that the *pipeline* produces a
 * coherent identity, not that a coherent identity passes a checker.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { CONSTRAINTS } from "../../src/__version__.js";
import { assertCoherent } from "../../src/clienthints.js";
import { IncoherentIdentity } from "../../src/exceptions.js";
import { AUTOMATION_SWITCHES, auditSwitches } from "../../src/switches.js";
import { launchOptions } from "../../src/utils.js";
import { backendMatchesOs } from "../../src/webgl/sample.js";

const REPO = path.resolve(
	path.dirname(fileURLToPath(import.meta.url)),
	"../..",
);

/** Reassemble the config from the chunked CAMOU_CONFIG_* environment. */
function configFrom(built: Record<string, unknown>): Record<string, unknown> {
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
		// No bundle is installed in CI; the launcher falls back to the pinned
		// milestone, which is what a matching install would have reported anyway.
		executable_path: "/nonexistent/camouwolf",
		...over,
	});
}

describe("version pin", () => {
	it("matches browser/upstream.sh", () => {
		// A launcher claiming a milestone the shipped browser is not would make
		// every identity's UA and Sec-CH-UA brand list disagree with the binary
		// that serves them — silently, since nothing else cross-checks the two.
		const up = fs.readFileSync(path.join(REPO, "browser/upstream.sh"), "utf8");
		const version = up.match(/^version=(.+)$/m)?.[1].trim();
		const milestone = up.match(/^milestone=(.+)$/m)?.[1].trim();
		expect(CONSTRAINTS.CHROMIUM_VERSION).toBe(version);
		expect(CONSTRAINTS.CHROMIUM_MILESTONE).toBe(Number(milestone));
	});

	it("has a milestone that is the version's major", () => {
		expect(String(CONSTRAINTS.CHROMIUM_MILESTONE)).toBe(
			CONSTRAINTS.CHROMIUM_VERSION.split(".")[0],
		);
	});
});

describe("identity coherence", () => {
	for (const os of ["windows", "macos", "linux"] as const) {
		it(`produces a coherent synthetic identity for ${os}`, async () => {
			const config = configFrom(await build({ os, allow_os_mismatch: true }));
			expect(assertCoherent(config)).toEqual([]);
		});

		it(`produces a coherent preset identity for ${os}`, async () => {
			const config = configFrom(
				await build({ os, fingerprint_preset: true, allow_os_mismatch: true }),
			);
			expect(assertCoherent(config)).toEqual([]);
		});
	}

	it("refuses an identity whose UA and hints disagree", async () => {
		await expect(
			build({
				config: {
					"navigator.userAgent":
						"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
						"(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
					"navigator.platform": "Win32",
					"userAgentData.platform": "Linux",
				},
			}),
		).rejects.toThrow(IncoherentIdentity);
	});

	it("lets i_know_what_im_doing override the refusal", async () => {
		const built = await build({
			i_know_what_im_doing: true,
			config: {
				"navigator.userAgent":
					"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
					"(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
				"navigator.platform": "Win32",
				"userAgentData.platform": "Linux",
			},
		});
		expect(configFrom(built)["userAgentData.platform"]).toBe("Linux");
	});
});

describe("screen geometry", () => {
	for (const os of ["windows", "macos", "linux"] as const) {
		it(`keeps a taskbar inset on ${os}`, async () => {
			// CreepJS: noTaskbar = (height === availHeight && width === availWidth)
			// is a Like-Headless flag, and it is exactly what stock headless
			// Chromium reports.
			const c = configFrom(await build({ os, allow_os_mismatch: true }));
			const sw = c["screen.width"] as number;
			const sh = c["screen.height"] as number;
			const aw = c["screen.availWidth"] as number;
			const ah = c["screen.availHeight"] as number;
			expect(aw === sw && ah === sh).toBe(false);
		});

		it(`keeps avail inside screen on ${os}`, async () => {
			const c = configFrom(await build({ os, allow_os_mismatch: true }));
			expect(c["screen.availWidth"] as number).toBeLessThanOrEqual(
				c["screen.width"] as number,
			);
			expect(c["screen.availHeight"] as number).toBeLessThanOrEqual(
				c["screen.height"] as number,
			);
		});
	}
});

describe("GPU adapter", () => {
	for (const os of ["windows", "macos", "linux"] as const) {
		it(`draws an ANGLE backend that ${os} actually uses`, async () => {
			const c = configFrom(await build({ os, allow_os_mismatch: true }));
			const renderer = c["webGl:renderer"] as string;
			expect(renderer).toBeTruthy();
			const osKey = { windows: "win", macos: "mac", linux: "lin" }[os];
			expect(backendMatchesOs(renderer, osKey)).toBe(true);
		});

		it(`never reports SwiftShader on ${os}`, async () => {
			// A SwiftShader renderer string is a hard headless tell — it is what a
			// GPU-less container falls back to.
			const c = configFrom(await build({ os, allow_os_mismatch: true }));
			expect(c["webGl:renderer"] as string).not.toMatch(
				/SwiftShader|llvmpipe/i,
			);
		});

		it(`ships numeric limits alongside the strings on ${os}`, async () => {
			// The strings alone are not the fingerprint: a detector cross-checks
			// MAX_TEXTURE_SIZE and friends against the claimed adapter.
			const c = configFrom(await build({ os, allow_os_mismatch: true }));
			const params = c["webGl:parameters"] as Record<string, number>;
			expect(params).toBeTruthy();
			expect(params["3379"]).toBeGreaterThanOrEqual(8192); // MAX_TEXTURE_SIZE
		});
	}

	it("refuses a renderer whose backend cannot exist on the target OS", async () => {
		await expect(
			build({
				os: "linux",
				allow_os_mismatch: true,
				webgl_config: [
					"Google Inc. (NVIDIA)",
					"ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
				],
			}),
		).rejects.toThrow(/ANGLE backend/);
	});
});

describe("seeding", () => {
	it("reproduces the same identity for the same seed", async () => {
		const a = configFrom(await build({ seed: 99, fingerprint_preset: true }));
		const b = configFrom(await build({ seed: 99, fingerprint_preset: true }));
		expect(a).toEqual(b);
	});

	it("produces a different identity for a different seed", async () => {
		const a = configFrom(await build({ seed: 1, fingerprint_preset: true }));
		const b = configFrom(await build({ seed: 2, fingerprint_preset: true }));
		expect(a).not.toEqual(b);
	});

	it("leaves canvas perturbation off by default", async () => {
		// Noise a detector can SEE is worse than none — it is a positive signal
		// rather than a neutral one. sundial's "C14: Canvas noise (14-color
		// roundtrip)" demands an exact pixel roundtrip and its arc-signature
		// table flags any unmodelled output as injected, so a perturbed canvas
		// fails both where an honest GPU-rendered one passes.
		//
		// The seed remains the single shared control for canvas, WebGL readback,
		// audio AND text-metric quantization (CamouwolfVisualSeed in
		// browser/additions/camouwolfcfg/camouwolf_farble.h) — one seed so a detector
		// that cross-checks two of those surfaces cannot catch them disagreeing.
		// Default 0 turns the whole set off together, which is the same
		// all-or-nothing property, just at the other end.
		const c = configFrom(await build({ seed: 5 }));
		expect(c["canvas:seed"]).toBeTypeOf("number");
		expect(c["canvas:seed"]).toBe(0);
	});
});

describe("switch hygiene", () => {
	it("strips every automation marker", async () => {
		const built = await build({
			i_know_what_im_doing: true,
			args: [...AUTOMATION_SWITCHES],
		});
		const args = built.args as string[];
		for (const marker of AUTOMATION_SWITCHES) {
			expect(args).not.toContain(marker);
		}
	});

	it("refuses --user-agent rather than silently ignoring it", async () => {
		// Silently dropping it would leave the caller believing they set the UA.
		await expect(
			build({ args: ["--user-agent=Mozilla/5.0 nope"] }),
		).rejects.toThrow(/--user-agent/);
	});

	it("tells Playwright not to re-add its own automation switches", async () => {
		const built = await build();
		expect(built.ignoreDefaultArgs).toContain("--enable-automation");
	});

	it("lets a caller's duplicate win over a base switch", () => {
		// Chromium resolves a repeated switch to its LAST occurrence, so base
		// switches must come first.
		const { args } = auditSwitches(["--no-first-run=override"]);
		expect(args.indexOf("--no-first-run=override")).toBeGreaterThan(
			args.indexOf("--no-default-browser-check"),
		);
	});
});

describe("config transport", () => {
	it("chunks the config into CAMOU_CONFIG_* and round-trips it", async () => {
		const built = await build();
		const env = built.env as Record<string, string>;
		expect(env.CAMOU_CONFIG_1).toBeTruthy();
		expect(() => configFrom(built)).not.toThrow();
	});

	it("never puts the identity on the command line", async () => {
		// A switch-borne persona is readable at /proc/<pid>/cmdline and in `ps`.
		const built = await build();
		const joined = (built.args as string[]).join(" ");
		expect(joined).not.toMatch(/Mozilla\/5\.0/);
		expect(joined).not.toMatch(/ANGLE \(/);
	});
});

describe("OS spoof policy", () => {
	it("refuses a host→target combo Camouwolf cannot render coherently", async () => {
		// The launcher runs on Linux in CI, so macOS is the unsupported target.
		if (process.platform !== "linux") return;
		await expect(build({ os: "macos" })).rejects.toThrow(/cannot spoof/);
	});
});
