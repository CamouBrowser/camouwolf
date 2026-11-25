/**
 * passthrough: true: the host's own fingerprint, with the behaviour options
 * kept. Twin of python/tests/test_passthrough.py.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ValueError } from "../src/exceptions.js";
import { MODEL } from "./fpgen-setup.js";
import {
	BUNDLE_EXE,
	configOf,
	HOME,
	restoreDeps,
	stubHost,
	utils,
	warnings,
} from "./launch-host.js";

const { NewBrowser, NewContext } = await import("../src/sync_api.js");

const PROXY = { server: "http://proxy.example:8080" };
const base = () => ({
	headless: true,
	executable_path: BUNDLE_EXE,
	exclude_addons: ["UBO" as const],
	env: { HOME },
	passthrough: true,
});

beforeEach(() => stubHost("lin"));
afterEach(() => restoreDeps());

describe.skipIf(!MODEL.ok)("passthrough", () => {
	it("sends no identity", async () => {
		const launch = await utils.launchOptions({
			...base(),
			block_webrtc: true,
			proxy: PROXY,
			i_know_what_im_doing: true,
		});
		expect(
			Object.keys(configOf(launch)).filter((key) => key.includes(".")),
		).toEqual([]);
		expect(
			launch.args.filter((arg: string) =>
				/^--(lang|accept-lang|window-)/.test(arg),
			),
		).toEqual([]);
		for (const name of ["FONTCONFIG_FILE", "TZ", "LANGUAGE"])
			expect(launch.env).not.toHaveProperty(name);
		expect(launch.proxy).toEqual(PROXY);
		expect(launch.args).toContain(utils.WEBRTC_BLOCK_SWITCH);
		expect(utils.launchIsPassthrough(launch)).toBe(true);
	});

	it("is not what a drawn launch is", async () => {
		const launch = await utils.launchOptions({
			...base(),
			passthrough: false,
			os: "linux",
		});
		expect(utils.launchIsPassthrough(launch)).toBe(false);
	});

	it.each([
		["os", "windows"],
		["locale", "de-DE"],
		["geoip", true],
		["config", { timezone: "Europe/Berlin" }],
		["canvas_noise", true],
	])("throws for %s alongside it", async (option, value) => {
		await expect(
			utils.launchOptions({ ...base(), [option]: value }),
		).rejects.toThrow(new RegExp(option));
	});

	it("warns that a proxy shows the host", async () => {
		const { warnings: caught } = await warnings.recordWarnings(() =>
			utils.launchOptions({ ...base(), proxy: PROXY }),
		);
		expect(caught.map((w) => w.category)).toContain("LeakWarning");
		expect(caught.map((w) => w.message).join("\n")).toMatch(/passthrough/);
	});

	it("keeps the host's identity in every context", async () => {
		const calls: any[] = [];
		const browser = {
			on() {},
			async newContext(options: any) {
				calls.push(options);
				return { pages: () => [], on() {} };
			},
		};
		const launched: any = await NewBrowser(
			{ launch: async () => browser } as any,
			base(),
		);
		const { warnings: caught } = await warnings.recordWarnings(() =>
			launched.newContext({ userAgent: "x" }),
		);
		expect(caught).toEqual([]);
		expect(calls[0].viewport).toBeNull();
		expect(calls[0]).not.toHaveProperty("geolocation");
		await expect(NewContext(launched)).rejects.toThrow(ValueError);
	});
});
