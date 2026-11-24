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

	it("leaves the brand list to Chromium", () => {
		// The public build claims Chromium; "Google Chrome" is a Pro feature.
		const config: Record<string, any> = {};
		ch.fillClientHints(config, "win", "158.0.8127.36");
		expect(config).not.toHaveProperty(["userAgentData.brands"]);
		expect(config).not.toHaveProperty(["userAgentData.fullVersionList"]);
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
