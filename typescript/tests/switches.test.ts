/**
 * Command-line hygiene: what is dropped, what is refused, what is added.
 * Twin of python/tests/test_switches.py.
 */
import * as fs from "node:fs";
import { createRequire } from "node:module";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { ValueError } from "../src/exceptions.js";
import * as switches from "../src/switches.js";

const { auditSwitches, mergeListSwitches } = switches;

describe("switches", () => {
	it("adds the base switches first", () => {
		const audit = auditSwitches(["--foo"]);
		expect(audit.args).toEqual([...switches.BASE_SWITCHES, "--foo"]);
		expect(audit.dropped).toEqual([]);
	});

	it.each([
		"--enable-automation",
		"--test-type",
		"--test-type=webdriver",
		"--use-mock-keychain",
	])("drops the automation marker %s", (arg) => {
		const audit = auditSwitches([arg]);
		expect(audit.args).not.toContain(arg);
		expect(audit.dropped).toEqual([[arg, "automation marker"]]);
	});

	it.each([
		"--disable-web-security",
		"--ignore-certificate-errors",
		"--disable-features=IsolateOrigins,site-per-process",
	])("drops %s, which a page can measure", (arg) => {
		const audit = auditSwitches([arg]);
		expect(audit.args).not.toContain(arg);
		expect(audit.dropped).toEqual([[arg, "changes web-visible behavior"]]);
	});

	it.each([
		["--user-agent=Mozilla/5.0", "config"],
		["--lang=de", "locale"],
		["--accept-lang=de", "locale"],
		["--window-size=800,600", "window"],
		["--window-position=0,0", "config"],
		["--force-device-scale-factor=2", "screen"],
		["--proxy-server=http://x", "proxy"],
		["--headless=new", "headless"],
		["--user-data-dir=/tmp/x", "persistent_context"],
	])("refuses %s, naming the `%s` option", (arg, option) => {
		let error: unknown;
		try {
			auditSwitches([arg]);
		} catch (e) {
			error = e;
		}
		expect(error).toBeInstanceOf(ValueError);
		expect((error as Error).message).toContain(
			`refuses '${arg.split("=")[0]}'`,
		);
		expect((error as Error).message).toContain(`\`${option}`);
		// Not strict: dropped, with the reason.
		const audit = auditSwitches([arg], false);
		expect(audit.args).not.toContain(arg);
		expect(audit.dropped[0][0]).toBe(arg);
	});

	it("merges repeated list switches, because Chromium reads the last", () => {
		expect(
			mergeListSwitches([
				"--disable-features=A,B",
				"--x",
				"--disable-features=B,C",
				"--enable-features=D",
			]),
		).toEqual(["--disable-features=A,B,C", "--x", "--enable-features=D"]);
	});

	it("joins a caller's blink feature to the base one", () => {
		const audit = auditSwitches(["--disable-blink-features=Foo"]);
		expect(
			audit.args.filter((a) => a.startsWith("--disable-blink-features=")),
		).toEqual(["--disable-blink-features=AutomationControlled,Foo"]);
	});

	it("does not double a caller's repeat of a base switch", () => {
		const audit = auditSwitches(["--no-first-run"]);
		expect(audit.args.filter((a) => a === "--no-first-run")).toHaveLength(1);
	});

	it("leaves out the Playwright defaults that mark automation", () => {
		expect(switches.IGNORED_DEFAULT_ARGS).toEqual(
			expect.arrayContaining([
				"--enable-automation",
				"--disable-extensions",
				"--disable-popup-blocking",
			]),
		);
	});

	it("restates the feature list the installed playwright-core disables", () => {
		// PLAYWRIGHT_DISABLED_FEATURES must be the list the installed Playwright
		// disables, or adding an extension would turn features back on.
		const lib = path.join(
			path.dirname(
				createRequire(import.meta.url).resolve("playwright-core/package.json"),
			),
			"lib",
		);
		const marker = "AvoidUnnecessaryBeforeUnloadCheckSync";
		const source = (
			fs.readdirSync(lib, { recursive: true, encoding: "utf8" }) as string[]
		)
			.filter((name) => name.endsWith(".js"))
			.map((name) => fs.readFileSync(path.join(lib, name), "utf8"))
			.find((text) => text.includes(marker));
		expect(
			source,
			"playwright-core states its disabled features",
		).toBeDefined();
		const start = (source as string).indexOf(marker);
		let block = (source as string).slice(
			Math.max(0, start - 200),
			start + 2500,
		);
		block = block.slice(0, block.indexOf("]"));
		const found = [...block.matchAll(/"([A-Za-z]+)"/g)]
			.map((m) => m[1])
			.filter((name) => name !== "darwin");
		expect(found.length).toBeGreaterThan(5);
		for (const name of found)
			expect(switches.PLAYWRIGHT_DISABLED_FEATURES).toContain(name);
	});
});
