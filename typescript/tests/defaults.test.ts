/**
 * camouwolf.defaults.json: the defaults every launch starts from, and the
 * user's own. Twin of python/tests/test_defaults.py.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MODEL } from "./fpgen-setup.js";
import {
	BUNDLE_EXE,
	configOf,
	HOME,
	restoreDeps,
	stubHost,
	utils,
} from "./launch-host.js";

const { DEFAULTS_FILE } = await import("../src/paths.js");

let tmp: string;
let saved: string | undefined;

const base = () => ({
	headless: true,
	executable_path: BUNDLE_EXE,
	exclude_addons: ["UBO" as const],
	env: { HOME },
});

/** Use `values` as the user's defaults file. */
function userDefaults(values: object): void {
	const file = path.join(tmp, "mine.json");
	fs.writeFileSync(file, JSON.stringify(values));
	process.env.CAMOUWOLF_DEFAULTS = file;
}

beforeEach(() => {
	tmp = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-defaults-"));
	saved = process.env.CAMOUWOLF_DEFAULTS;
	stubHost("lin");
});

afterEach(() => {
	process.env.CAMOUWOLF_DEFAULTS = saved;
	restoreDeps();
	fs.rmSync(tmp, { recursive: true, force: true });
});

it("is the Python launcher's file", () => {
	expect(fs.readFileSync(DEFAULTS_FILE, "utf8")).toBe(
		fs.readFileSync(
			path.join(
				import.meta.dirname,
				"..",
				"..",
				"python",
				"src",
				"camouwolf",
				"camouwolf.defaults.json",
			),
			"utf8",
		),
	);
});

describe.skipIf(!MODEL.ok)("launchOptions", () => {
	it("takes an unset option from the user's file", async () => {
		userDefaults({ block_webrtc: true });
		const launch = await utils.launchOptions({ ...base(), os: "linux" });
		expect(launch.args).toContain(utils.WEBRTC_BLOCK_SWITCH);
	});

	it("lets an explicit option win over the user's file", async () => {
		userDefaults({ block_webrtc: true, os: "windows" });
		const launch = await utils.launchOptions({
			...base(),
			os: "linux",
			block_webrtc: false,
		});
		expect(launch.args).not.toContain(utils.WEBRTC_BLOCK_SWITCH);
		expect(configOf(launch)["navigator.userAgent"]).toContain("Linux");
	});

	it("refuses a typo in the user's file", async () => {
		userDefaults({ block_webrtcc: true });
		await expect(
			utils.launchOptions({ ...base(), os: "linux" }),
		).rejects.toThrow(/block_webrtcc/);
	});
});
