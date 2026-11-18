/**
 * Client-hint coherence.
 *
 * This is the surface with no Firefox equivalent and the one most likely to
 * give a spoofed Chrome away, so it gets the most tests. Each case here is a
 * real detection: something a page or a server can check in one step.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
	assertCoherent,
	buildBrandList,
	buildUserAgent,
	type ChromeIdentity,
	clientHintConfig,
	greaseBrand,
	PLATFORM_VERSIONS,
	retargetUserAgent,
	serializeBrandList,
} from "../../src/clienthints.js";

const REPO = path.resolve(
	path.dirname(fileURLToPath(import.meta.url)),
	"../..",
);

const win: ChromeIdentity = {
	milestone: 152,
	fullVersion: "152.0.7977.42",
	os: "win",
	platformVersion: "15.0.0",
};
const mac: ChromeIdentity = { ...win, os: "mac", platformVersion: "15.3.1" };
const lin: ChromeIdentity = { ...win, os: "lin", platformVersion: "6.12.0" };

describe("buildUserAgent", () => {
	it("uses the frozen platform tokens, not the live OS version", () => {
		// Chrome's UA reduction pinned these years ago. A UA carrying a real OS
		// version ("Windows NT 11.0", "Mac OS X 14_5") is a shape no shipping
		// Chrome emits — the mistake almost every hand-rolled spoofer makes,
		// because a live version looks more honest.
		expect(buildUserAgent(win)).toContain("Windows NT 10.0; Win64; x64");
		expect(buildUserAgent(mac)).toContain("Intel Mac OS X 10_15_7");
		expect(buildUserAgent(lin)).toContain("X11; Linux x86_64");
	});

	it("reports a zeroed minor version", () => {
		// Chrome freezes the UA's minor/build/patch at 0.0.0; the real build only
		// appears in Sec-CH-UA-Full-Version-List.
		expect(buildUserAgent(win)).toContain("Chrome/152.0.0.0");
		expect(buildUserAgent(win)).not.toContain("7977");
	});

	it("is byte-identical to the shape stock Chrome emits", () => {
		expect(buildUserAgent(win)).toBe(
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
				"(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
		);
	});
});

describe("retargetUserAgent", () => {
	it("moves the milestone and leaves everything else alone", () => {
		const old =
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
			"(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36";
		const out = retargetUserAgent(old, 152);
		expect(out).toContain("Chrome/152.0.0.0");
		expect(out).toContain("Windows NT 10.0");
		expect(out).toContain("Safari/537.36");
	});

	it("does not touch the Safari or AppleWebKit versions", () => {
		// Both are frozen constants; rewriting them would be its own tell.
		const out = retargetUserAgent(buildUserAgent(win), 160);
		expect(out).toContain("AppleWebKit/537.36");
		expect(out).toContain("Safari/537.36");
	});
});

describe("brand list", () => {
	it("always contains a GREASE entry", () => {
		// A brand list without one is not a list Chrome produces — and omitting it
		// is the common shortcut, which makes its absence a reliable signal.
		const brands = buildBrandList(win);
		expect(brands).toHaveLength(3);
		expect(brands.some((b) => /Not.?A.?Brand/.test(b.brand))).toBe(true);
	});

	it("contains both Chromium and the consumer brand", () => {
		const names = buildBrandList(win).map((b) => b.brand);
		expect(names).toContain("Chromium");
		expect(names).toContain("Google Chrome");
	});

	it("is stable for a milestone, not random per call", () => {
		// Two real Chromes of the same milestone agree. If the GREASE entry or the
		// permutation were drawn per launch, two "identical" identities would
		// disagree — a linkability signal in reverse.
		expect(buildBrandList(win)).toEqual(buildBrandList({ ...win }));
		expect(greaseBrand(152)).toEqual(greaseBrand(152));
	});

	it("varies between milestones", () => {
		const a = serializeBrandList(buildBrandList({ ...win, milestone: 151 }));
		const b = serializeBrandList(buildBrandList({ ...win, milestone: 152 }));
		expect(a).not.toBe(b);
	});

	it("serializes in the structured-header shape Sec-CH-UA uses", () => {
		const header = serializeBrandList(buildBrandList(win));
		expect(header).toMatch(/^"[^"]+";v="[^"]+"(, "[^"]+";v="[^"]+")*$/);
	});

	it("uses the full version in the full-version list", () => {
		const full = serializeBrandList(buildBrandList(win, true));
		expect(full).toContain("152.0.7977.42");
	});
});

describe("assertCoherent", () => {
	it("passes a generated identity on every platform", () => {
		for (const id of [win, mac, lin]) {
			expect(assertCoherent(clientHintConfig(id))).toEqual([]);
		}
	});

	it("catches a UA/Sec-CH-UA-Platform mismatch", () => {
		// The headline case: exactly what `--user-agent` produces.
		const cfg = clientHintConfig(win);
		cfg["userAgentData.platform"] = "Linux";
		expect(assertCoherent(cfg).join(" ")).toMatch(/Sec-CH-UA-Platform/);
	});

	it("catches a navigator.platform mismatch", () => {
		const cfg = clientHintConfig(win);
		cfg["navigator.platform"] = "MacIntel";
		expect(assertCoherent(cfg).join(" ")).toMatch(/navigator\.platform/);
	});

	it("catches a platformVersion off the Windows UA-CH ladder", () => {
		// Windows reports a ladder value, NOT the marketing version and NOT the
		// raw build number. "11.0.0" is skipped entirely; "22631.0.0" is a build.
		for (const bad of ["11.0.0", "22631.0.0", "10.0.19045"]) {
			const cfg = clientHintConfig(win);
			cfg["userAgentData.platformVersion"] = bad;
			expect(assertCoherent(cfg).join(" ")).toMatch(/ladder|not a value/);
		}
	});

	it("accepts every value on each platform's ladder", () => {
		for (const [os, id] of [
			["win", win],
			["mac", mac],
			["lin", lin],
		] as const) {
			for (const pv of PLATFORM_VERSIONS[os]) {
				const cfg = clientHintConfig({ ...id, platformVersion: pv });
				expect(assertCoherent(cfg)).toEqual([]);
			}
		}
	});

	it("keeps the generator's ladder in sync with this one", () => {
		// The preset generator carries its own copy of PLATFORM_VERSIONS. If the
		// two drift, the generator draws a rung assertCoherent() rejects and the
		// whole pool becomes unlaunchable — with the failure landing at launch
		// time, far from the edit that caused it.
		const gen = fs.readFileSync(
			path.join(REPO, "scripts/gen-fingerprint-presets.mjs"),
			"utf8",
		);
		const block = gen.match(/const PLATFORM_VERSIONS = \{([\s\S]*?)\n\};/)?.[1];
		expect(block, "generator ladder not found").toBeTruthy();
		for (const [genOs, os] of [
			["windows", "win"],
			["macos", "mac"],
			["linux", "lin"],
		] as const) {
			const line = block?.match(
				new RegExp(`${genOs}:\\s*\\[([^\\]]*)\\]`),
			)?.[1];
			expect(line, `generator has no ${genOs} ladder`).toBeTruthy();
			const values = [...(line ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
			expect(values, `${genOs} ladder drifted`).toEqual(PLATFORM_VERSIONS[os]);
		}
	});

	it("can express a current Windows 11", () => {
		// The measured value from the real Win11 box (build 26200). The ladder
		// used to stop at "15.0.0", so no persona could claim a shipping Windows.
		expect(PLATFORM_VERSIONS.win).toContain("19.0.0");
		const cfg = clientHintConfig({ ...win, platformVersion: "19.0.0" });
		expect(assertCoherent(cfg)).toEqual([]);
	});

	it("catches a mobile bit on a desktop UA", () => {
		// Sec-CH-UA-Mobile rides on every request with no opt-in, so a desktop UA
		// arriving with ?1 fails before any JS runs.
		const cfg = clientHintConfig(win);
		cfg["userAgentData.mobile"] = true;
		expect(assertCoherent(cfg).join(" ")).toMatch(/Mobile/);
	});

	it("catches WoW64 claimed alongside 64-bit", () => {
		const cfg = clientHintConfig(win);
		cfg["userAgentData.wow64"] = true;
		expect(assertCoherent(cfg).join(" ")).toMatch(/WoW64/);
	});

	it("catches a populated model on a desktop UA", () => {
		const cfg = clientHintConfig(win);
		cfg["userAgentData.model"] = "Pixel 8";
		expect(assertCoherent(cfg).join(" ")).toMatch(/Model/);
	});

	it("catches a full version whose major disagrees with the UA", () => {
		const cfg = clientHintConfig(win);
		cfg["userAgentData.uaFullVersion"] = "151.0.7922.137";
		expect(assertCoherent(cfg).join(" ")).toMatch(/Full-Version-List/);
	});

	it("returns nothing when there is no UA to check against", () => {
		// A partial config is not an incoherent one; only a contradiction is.
		expect(assertCoherent({ "userAgentData.platform": "Windows" })).toEqual([]);
	});
});
