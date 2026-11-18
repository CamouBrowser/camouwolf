/**
 * `extensions` path validation.
 *
 * Chromium does not fail a launch over a bad `--load-extension` path — it logs
 * a line to stderr, which Playwright swallows, and starts without the
 * extension. These tests pin the launcher's refusal to pass an unloadable path
 * through, because the failure it prevents is silent: the browser comes up, the
 * ad blocker or captcha helper simply is not there, and nothing says so.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import { InvalidExtensionPath } from "../../src/exceptions.js";
import { confirmExtensionPaths } from "../../src/utils.js";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-ext-test-"));
afterAll(() => fs.rmSync(tmp, { recursive: true, force: true }));

/** A loadable unpacked extension: a directory holding a manifest.json. */
function goodExtension(name: string): string {
	const dir = path.join(tmp, name);
	fs.mkdirSync(dir, { recursive: true });
	fs.writeFileSync(
		path.join(dir, "manifest.json"),
		JSON.stringify({ manifest_version: 3, name, version: "1.0" }),
	);
	return dir;
}

describe("confirmExtensionPaths", () => {
	it("returns absolute paths", () => {
		const dir = goodExtension("ok");
		const [resolved] = confirmExtensionPaths([dir]);
		expect(path.isAbsolute(resolved)).toBe(true);
		expect(resolved).toBe(dir);
	});

	it("resolves a relative path against the working directory", () => {
		const dir = goodExtension("relative");
		const rel = path.relative(process.cwd(), dir);
		expect(confirmExtensionPaths([rel])).toEqual([dir]);
	});

	it("accepts several at once, preserving order", () => {
		const a = goodExtension("first");
		const b = goodExtension("second");
		expect(confirmExtensionPaths([a, b])).toEqual([a, b]);
	});

	it("accepts an empty list", () => {
		expect(confirmExtensionPaths([])).toEqual([]);
	});

	it("throws on a path that does not exist", () => {
		expect(() => confirmExtensionPaths([path.join(tmp, "nope")])).toThrow(
			InvalidExtensionPath,
		);
	});

	it("throws when handed a file rather than a directory", () => {
		// The .crx case: --load-extension takes an unpacked directory and ignores
		// a packed archive without saying so.
		const crx = path.join(tmp, "packed.crx");
		fs.writeFileSync(crx, "not really a crx");
		expect(() => confirmExtensionPaths([crx])).toThrow(InvalidExtensionPath);
	});

	it("throws when the directory has no manifest.json", () => {
		// The common mistake: pointing at the repo root rather than at the
		// directory the manifest lives in.
		const dir = path.join(tmp, "no-manifest");
		fs.mkdirSync(dir, { recursive: true });
		expect(() => confirmExtensionPaths([dir])).toThrow(InvalidExtensionPath);
	});

	it("names the offending path in the message", () => {
		const missing = path.join(tmp, "typo-here");
		expect(() => confirmExtensionPaths([missing])).toThrow(/typo-here/);
	});

	it("rejects the whole list when one entry is bad", () => {
		// Fail the launch rather than loading a subset: a caller who asked for two
		// extensions and silently got one is in the same position as a caller who
		// silently got none.
		const good = goodExtension("valid");
		expect(() =>
			confirmExtensionPaths([good, path.join(tmp, "absent")]),
		).toThrow(InvalidExtensionPath);
	});
});
