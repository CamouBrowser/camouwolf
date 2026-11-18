/**
 * The user agent and the client hints come from one identity and must agree.
 * Twin of python/tests/test_clienthints.py.
 */
import { describe, expect, it } from "vitest";
import * as ch from "../src/clienthints.js";

const identity = (os: ch.OsKey): ch.ChromeIdentity => ({
	milestone: 152,
	fullVersion: "152.0.7977.42",
	os,
});
const windows = () => ch.clientHintConfig(identity("win"));

describe("client hints", () => {
	it.each([
		["win", "Windows NT 10.0; Win64; x64", "Win32", "Windows"],
		["mac", "Macintosh; Intel Mac OS X 10_15_7", "MacIntel", "macOS"],
		["lin", "X11; Linux x86_64", "Linux x86_64", "Linux"],
	] as const)("one %s identity gives agreeing values", (osKey, token, platform, chPlatform) => {
		const config = ch.clientHintConfig(identity(osKey));
		expect(config["navigator.userAgent"]).toBe(
			`Mozilla/5.0 (${token}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36`,
		);
		expect(config["navigator.platform"]).toBe(platform);
		expect(config["userAgentData.platform"]).toBe(chPlatform);
		expect(config["userAgentData.uaFullVersion"]).toBe("152.0.7977.42");
		expect(ch.PLATFORM_VERSIONS[osKey]).toContain(
			config["userAgentData.platformVersion"],
		);
		expect(config["userAgentData.architecture"]).toBe(
			osKey === "mac" ? "arm" : "x86",
		);
		expect(config["userAgentData.model"]).toBe("");
		expect(config["userAgentData.mobile"]).toBe(false);
		expect(ch.uaOs(config["navigator.userAgent"])).toBe(osKey);
		expect(ch.assertCoherent(config)).toEqual([]);
	});

	it("puts only the reduced version in the user agent", () => {
		const ua = ch.buildUserAgent(identity("win"));
		expect(ua).toContain("Chrome/152.0.0.0");
		expect(ua).not.toContain("7977");
	});

	it("moves a recorded user agent to the running milestone", () => {
		const recorded =
			"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36";
		expect(ch.retargetUserAgent(recorded, 152)).toContain(
			"Chrome/152.0.0.0 Safari",
		);
	});

	it("builds a brand list of Chromium, Chrome and one GREASE entry", () => {
		const brands = ch.buildBrandList(identity("win"));
		const names = brands.map((b) => b.brand);
		expect(names).toEqual(
			expect.arrayContaining(["Chromium", "Google Chrome"]),
		);
		expect(brands).toHaveLength(3);
		expect(names).toContain(ch.greaseBrand(152).brand);
		expect(
			new Set(
				brands
					.filter((b) => ["Chromium", "Google Chrome"].includes(b.brand))
					.map((b) => b.version),
			),
		).toEqual(new Set(["152"]));
		const full = ch.buildBrandList(identity("win"), true);
		expect(full.find((b) => b.brand === "Chromium")?.version).toBe(
			"152.0.7977.42",
		);
		expect(ch.serializeBrandList(brands).split(';v="')).toHaveLength(4);
	});

	it("derives the GREASE brand from the milestone", () => {
		expect(ch.greaseBrand(152)).toEqual(ch.greaseBrand(152));
		const milestones = Array.from({ length: 20 }, (_, i) => 140 + i);
		expect(
			new Set(milestones.map((m) => ch.greaseBrand(m).brand)).size,
		).toBeGreaterThan(1);
		for (const m of milestones)
			expect(["8", "24", "99"]).toContain(ch.greaseBrand(m).version);
	});

	it("fill keeps what the config already holds", () => {
		const config: Record<string, any> = {
			"navigator.userAgent": "x",
			"userAgentData.platformVersion": "19.0.0",
		};
		ch.fillClientHints(config, "win", "152.0.7977.42");
		expect(config["navigator.userAgent"]).toBe("x");
		expect(config["userAgentData.platformVersion"]).toBe("19.0.0");
		expect(config["userAgentData.platform"]).toBe("Windows");
	});

	it("fill carries Chrome's brand lists", () => {
		const config: Record<string, any> = {};
		ch.fillClientHints(config, "win", "158.0.8127.36");
		const brands: ch.Brand[] = config["userAgentData.brands"];
		const real = (list: ch.Brand[]) =>
			list.filter((b) => !b.brand.includes("Not"));
		expect(
			real(brands)
				.map((b) => b.brand)
				.sort(),
		).toEqual(["Chromium", "Google Chrome"]);
		expect(new Set(real(brands).map((b) => b.version))).toEqual(
			new Set(["158"]),
		);
		const full: ch.Brand[] = config["userAgentData.fullVersionList"];
		expect(full.map((b) => b.brand)).toEqual(brands.map((b) => b.brand));
		expect(new Set(real(full).map((b) => b.version))).toEqual(
			new Set(["158.0.8127.36"]),
		);
		const kept = { "userAgentData.brands": [{ brand: "x", version: "1" }] };
		ch.fillClientHints(kept, "win", "158.0.8127.36");
		expect(kept["userAgentData.brands"]).toEqual([
			{ brand: "x", version: "1" },
		]);
	});

	it.each([
		[{ "userAgentData.platform": "Linux" }, "Sec-CH-UA-Platform"],
		[{ "navigator.platform": "MacIntel" }, "navigator.platform"],
		[{ "userAgentData.platformVersion": "11.0.0" }, "Platform-Version"],
		[{ "userAgentData.mobile": true }, "Mobile"],
		[{ "userAgentData.wow64": true }, "WoW64"],
		[{ "userAgentData.model": "Pixel 9" }, "Model"],
		[{ "userAgentData.uaFullVersion": "151.0.1.2" }, "Full-Version"],
	])("reports the contradiction %j", (change, problem) => {
		const problems = ch.assertCoherent({ ...windows(), ...change });
		expect(problems).toHaveLength(1);
		expect(problems[0]).toContain(problem);
	});

	it("has nothing to contradict without a user agent", () => {
		expect(ch.assertCoherent({ "userAgentData.platform": "Linux" })).toEqual(
			[],
		);
	});
});
