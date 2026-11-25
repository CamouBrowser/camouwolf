/**
 * Port of the CLI test in python/tests/test_defaults.py: `camouwolf defaults`
 * writes the user's copy of the defaults file and prints its path.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

let tmp: string;
let saved: string | undefined;
let savedArgv: string[];

beforeEach(() => {
	tmp = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-cli-"));
	saved = process.env.CAMOUWOLF_DEFAULTS;
	savedArgv = process.argv;
	vi.resetModules();
});

afterEach(() => {
	process.env.CAMOUWOLF_DEFAULTS = saved;
	process.argv = savedArgv;
	vi.restoreAllMocks();
	fs.rmSync(tmp, { recursive: true, force: true });
});

it("defaults copies the packaged file and prints the copy's path", async () => {
	const mine = path.join(tmp, "mine.json");
	process.env.CAMOUWOLF_DEFAULTS = mine;
	let output = "";
	vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
		output += String(chunk);
		return true;
	});
	process.argv = ["node", "camouwolf", "defaults"];
	await import("../src/__main__.js");
	await new Promise((resolve) => setTimeout(resolve, 0));
	vi.mocked(process.stdout.write).mockRestore();

	const { DEFAULTS_FILE } = await import("../src/paths.js");
	expect(process.exitCode ?? 0).toBe(0);
	expect(output.trim()).toBe(mine);
	expect(fs.readFileSync(mine, "utf8")).toBe(
		fs.readFileSync(DEFAULTS_FILE, "utf8"),
	);
});
