/**
 * The package ships what the Python package ships, at the same version.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { LIBRARY_VERSION } from "../src/__version__.js";
import { FRAME_ORIGIN, VIEWPORT } from "../src/_humanize_playwright.js";
import { FILL_NEEDS_CLICK } from "../src/driver_defaults.js";
import { DATA_FILES, LOCAL_DATA } from "../src/paths.js";
import { prerequisite } from "./prereq.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(HERE, "..");
const PYLIB = path.resolve(PKG, "..", "python");
const pkg = JSON.parse(
	fs.readFileSync(path.join(PKG, "package.json"), "utf-8"),
);
const pyproject = fs.readFileSync(path.join(PYLIB, "pyproject.toml"), "utf-8");

describe("package.json tracks pyproject.toml", () => {
	it("has the same version, and __version__.ts agrees", () => {
		const version = pyproject.match(/^version = "([^"]+)"/m)?.[1];
		expect(pkg.version).toBe(version);
		expect(LIBRARY_VERSION).toBe(version);
	});

	it("caps playwright-core where pyproject caps playwright", () => {
		const cap = pyproject.match(/^playwright = "([^"]+)"/m)?.[1];
		expect(cap).toBeDefined();
		expect(pkg.peerDependencies["playwright-core"]).toBe(cap);
		const installed = JSON.parse(
			fs.readFileSync(
				path.join(PKG, "node_modules", "playwright-core", "package.json"),
				"utf-8",
			),
		).version as string;
		const [major, minor] = installed.split(".").map(Number);
		const [capMajor, capMinor] = String(cap)
			.replace("<", "")
			.split(".")
			.map(Number);
		expect(major * 1000 + minor).toBeLessThan(capMajor * 1000 + capMinor);
	});

	it("builds with the Python package's data files, byte for byte", () => {
		// `pnpm build` copies them (scripts/copy-files.mjs); a copy that differs
		// from python/src/camouwolf is a stale or edited one.
		const built = path.join(PKG, "dist", "data-files");
		if (!prerequisite("build", fs.existsSync(built), "run pnpm build")) return;
		for (const name of DATA_FILES) {
			expect(
				fs
					.readFileSync(path.join(built, name))
					.equals(fs.readFileSync(path.join(PYLIB, "src", "camouwolf", name))),
				`dist/data-files/${name} differs from python/src/camouwolf/${name}`,
			).toBe(true);
		}
		const extra = fs
			.readdirSync(built, { recursive: true, withFileTypes: true })
			.filter((entry) => entry.isFile())
			.map((entry) =>
				path.relative(built, path.join(entry.parentPath, entry.name)),
			);
		expect(extra.sort()).toEqual([...DATA_FILES].sort());
	});

	it("reads every data file from the Python package, the one copy", () => {
		expect(path.resolve(LOCAL_DATA)).toBe(path.join(PYLIB, "src", "camouwolf"));
		for (const name of DATA_FILES)
			expect(fs.existsSync(path.join(LOCAL_DATA, name)), name).toBe(true);
	});
});

describe("the page scripts are the Python package's, byte for byte", () => {
	const constant = (file: string, name: string) => {
		const source = fs.readFileSync(
			path.join(PYLIB, "src", "camouwolf", file),
			"utf-8",
		);
		const match =
			source.match(new RegExp(`^${name} = """([^]*?)"""$`, "m")) ??
			source.match(new RegExp(`^${name} = '([^'\n]*)'$`, "m"));
		return match?.[1];
	};

	it.each([
		["driver_defaults.py", "FILL_NEEDS_CLICK", FILL_NEEDS_CLICK],
		["_humanize_playwright.py", "_VIEWPORT", VIEWPORT],
		["_humanize_playwright.py", "_FRAME_ORIGIN", FRAME_ORIGIN],
	])("%s %s", (file, name, script) => {
		expect(script).toBe(constant(file, name));
	});
});
