/**
 * Drive a local sundial at a Camouwolf build and read back its verdict.
 *
 * **Sundial is the oracle.** Holo's hardest-won lesson was to stop reinventing
 * fingerprint checks: sundial (daijro/sundial, run locally) bundles CreepJS,
 * fpworker, reversed Browserscan and Pixelscan, Browserleaks, Scrapfly's tools,
 * the rebrowser detector and its own cross-signal consistency tests — roughly
 * 760 checks that are maintained by people whose job is finding these. Anything
 * we write ourselves is a weaker copy that goes stale.
 *
 * The same repo serves Camouwolf: sundial's checks are browser-agnostic, and its
 * `osConsistent` / rasterizer / trig detectors are exactly what a Chromium
 * OS-spoof has to survive.
 *
 * Run:
 *   # start sundial's dev server first (see $SUNDIAL_DIR/README.md)
 *   node tests/compare/sundial-drive.mjs --url http://localhost:5173
 *
 * Env:
 *   SUNDIAL_URL   default http://localhost:5173
 *   CAMOUWOLF_EXECUTABLE_PATH, CAMOUWOLF_GPU  (see tests/lib/drive.mjs)
 */
import fs from "node:fs";
import path from "node:path";

import { Report, withPage } from "../lib/drive.mjs";

const argv = process.argv.slice(2);
const arg = (n, d) => {
	const i = argv.indexOf(n);
	return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const URL_BASE = arg("--url", process.env.SUNDIAL_URL || "http://localhost:5173");
const os = arg("--os", "windows");
const seed = Number(arg("--seed", "1"));
const OUT = arg("--out", "");

const report = new Report(`sundial (${os})`);

await withPage(
	async (page) => {
		// ?auto=1 makes sundial run every test on load and publish the result on
		// window.fullReport rather than waiting for a click.
		// NOT networkidle: ?auto=1 starts the whole battery on load, so the page
		// is deliberately network-busy for minutes. networkidle cannot settle and
		// goto times out before a single test has been read. domcontentloaded is
		// enough — waitForFunction below is what actually waits for the result.
		await page.goto(`${URL_BASE}/?auto=1`, { waitUntil: "domcontentloaded" });

		// Sundial's slowest detectors (the rasterizer and trig batteries) take a
		// while; poll rather than guessing a timeout.
		const full = await page.waitForFunction(
			() => (window.fullReport?.summary?.total ? window.fullReport : null),
			null,
			{ timeout: 180_000, polling: 1000 },
		);
		const result = await full.jsonValue();

		// fullReport carries no flat `tests` array. Entries are bucketed by status
		// and then by category:
		//   { failures: { Graphics: [entry, ...], ... }, succeeded: {...},
		//     pending: {...}, skipped: {...} }
		// `failures` already folds in `error`, because sundial counts a detector
		// that threw as a leak too.
		const flatten = (bucket) => Object.values(bucket ?? {}).flat();
		const tests = ["failures", "succeeded", "pending", "skipped"].flatMap((b) =>
			flatten(result[b]),
		);
		const failed = flatten(result.failures);
		console.log(`  sundial ran ${tests.length} tests, ${failed.length} failing`);

		if (OUT) {
			fs.mkdirSync(path.dirname(OUT), { recursive: true });
			fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
			console.log(`  wrote ${OUT}`);
		}

		// The blocking signals. These are the ones that mean the OS spoof itself
		// failed, rather than a cosmetic difference:
		//
		//  - osConsistent: sundial's own cross-signal check that every OS-derived
		//    surface tells the same story. This is the single most important bit
		//    in the whole report.
		//  - rasterizer: the glyph rasterizer identified from rendered text. A
		//    Windows persona whose text was rasterized by FreeType is a Linux box
		//    wearing a Windows UA, and no amount of string spoofing hides it.
		//  - trig: libm identification from Math.sin/cos/tan low bits. Same idea,
		//    via the C library rather than the font stack.
		// osConsistent lives on `identity`, NOT on `summary`, and there is no
		// `osSignals` key at all. Read from summary it came back undefined, and
		// `undefined !== false` is true — so the single most important gate in
		// this file passed vacuously no matter what the browser did.
		const identity = result.identity ?? {};
		report.check(
			identity.osConsistent !== false,
			"sundial osConsistent",
			JSON.stringify({
				os: identity.os,
				osDetected: identity.osDetected,
				uaMismatch: identity.uaMismatch,
				engineMismatch: identity.engineMismatch,
			}),
		);

		const named = (needle) =>
			failed.filter((t) => new RegExp(needle, "i").test(t.name ?? ""));

		report.check(
			named("rasterizer|freetype|directwrite").length === 0,
			"no rasterizer mismatch",
			named("rasterizer|freetype|directwrite").map((t) => t.name).join(", "),
		);
		report.check(
			named("trig|fdlibm|ucrt|math").length === 0,
			"no libm/trig mismatch",
			named("trig|fdlibm|ucrt|math").map((t) => t.name).join(", "),
		);
		report.check(
			named("headless|automation|webdriver|cdp|runtime.enable").length === 0,
			"no automation detection",
			named("headless|automation|webdriver|cdp|runtime.enable")
				.map((t) => t.name)
				.join(", "),
		);
		report.check(
			named("client.?hint|sec-ch-ua|useragentdata").length === 0,
			"no client-hint inconsistency",
			named("client.?hint|sec-ch-ua|useragentdata").map((t) => t.name).join(", "),
		);

		// Everything else is reported but not blocking — sundial includes checks
		// that flag legitimate configurations, and treating the raw count as a
		// gate would make the suite un-greenable and therefore ignored.
		if (failed.length) {
			console.log("\n  non-blocking failures:");
			for (const t of failed) console.log(`    - ${t.name}`);
		}
	},
	{ os, seed, fingerprint_preset: true },
);

report.finish();
