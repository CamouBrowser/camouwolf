/**
 * Capture one canonical fingerprint from one browser.
 *
 * Three modes, one probe. The value of the exercise depends entirely on the
 * three runs being measured the same way, so everything that could differ
 * between them — the probe source, the origin the page is served from, the
 * request the browser makes to get it — is shared here rather than reimplemented
 * per target.
 *
 *   --mode local     a locally-installed Chrome/Chromium, WINDOWED on the real
 *                    display. Never headless: headless changes real values
 *                    (ActiveText, viewport==screen) and is its own tell.
 *   --mode cdp       an already-running browser reachable over CDP. Used for
 *                    real Chrome on the Windows box, driven through an SSH
 *                    tunnel so it stays windowed on its own desktop.
 *   --mode camouwolf     the Camouwolf build, launched through the launcher so the
 *                    identity actually applies.
 *
 * The page is served from a loopback HTTP server on THIS machine — for the cdp
 * mode, reached through an `ssh -R` reverse tunnel — for two reasons: 127.0.0.1
 * is a secure context (so crypto.subtle exists, as it does for real users on
 * https), and the server sees the browser's real request headers, which is the
 * only place Sec-CH-UA and header ORDER are observable at all.
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
	CODECS, DUMP, FEATURE_PROBES, MATH_INPUTS, MEDIA_QUERIES,
	PROBE_FONTS, SYSTEM_COLORS, SYSTEM_FONTS,
} from "../lib/fingerprint-dump.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const argv = process.argv.slice(2);
const arg = (n, d) => {
	const i = argv.indexOf(n);
	return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const has = (n) => argv.includes(n);

const mode = arg("--mode", "local");
const outPath = arg("--out", null);
const originPort = Number(arg("--origin-port", "8899"));
const cdpUrl = arg("--cdp", "http://127.0.0.1:9222");
const execPath = arg("--exec", null);
const label = arg("--label", mode);

const OPTS = {
	fonts: PROBE_FONTS, systemColors: SYSTEM_COLORS, systemFonts: SYSTEM_FONTS,
	mediaQueries: MEDIA_QUERIES, features: FEATURE_PROBES, codecs: CODECS,
	mathInputs: MATH_INPUTS,
};

/** Serve the probe page and record the request the browser actually made. */
async function origin(port) {
	const seen = [];
	const server = http.createServer((req, res) => {
		seen.push({
			url: req.url,
			method: req.method,
			httpVersion: req.httpVersion,
			// rawHeaders preserves ORDER and original casing. Header order is a
			// stable, widely-used fingerprint that no JS read can reach.
			rawHeaders: req.rawHeaders.filter((_, i) => i % 2 === 0),
			headers: req.headers,
		});
		if (req.url === "/favicon.ico") { res.writeHead(404); res.end(); return; }
		res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
		res.end("<!doctype html><html><head><title>fp</title></head><body>probe</body></html>");
	});
	await new Promise((r) => server.listen(port, "127.0.0.1", r));
	return { url: `http://127.0.0.1:${port}/`, seen, close: () => server.close() };
}

async function run() {
	const { chromium } = await import("playwright-core");
	const org = await origin(originPort);
	let browser = null;
	let context = null;
	let closeBrowser = async () => {};

	try {
		if (mode === "cdp") {
			browser = await chromium.connectOverCDP(cdpUrl);
			context = browser.contexts()[0] || (await browser.newContext());
			closeBrowser = async () => { await browser.close().catch(() => {}); };
		} else if (mode === "camouwolf") {
			const { Camouwolf } = await import(path.join(REPO, "dist/index.js"));
			browser = await Camouwolf({
				headless: process.env.CAMOUWOLF_HEADLESS_MODE || (process.env.CAMOUWOLF_GPU ? "virtual-gpu" : "virtual"),
				executable_path: execPath || process.env.CAMOUWOLF_EXECUTABLE_PATH,
				gpu: process.env.CAMOUWOLF_GPU,
				os: arg("--os", "windows"),
				seed: Number(arg("--seed", "1")),
				stealth: true,
				fingerprint_preset: true,
			});
			context = browser.contexts()[0] || (await browser.newContext());
			closeBrowser = async () => { await browser.close().catch(() => {}); };
		} else {
			if (!execPath) throw new Error("--mode local needs --exec <path to chrome>");
			// Windowed on the real display. The only switches passed are the ones
			// that stop a throwaway profile from showing first-run UI; none of them
			// touches a fingerprint surface.
			browser = await chromium.launch({
				executablePath: execPath,
				headless: false,
				args: ["--no-first-run", "--no-default-browser-check",
					"--disable-search-engine-choice-screen"],
			});
			context = await browser.newContext({ viewport: null });
			closeBrowser = async () => { await browser.close().catch(() => {}); };
		}

		const page = await context.newPage();
		await page.goto(org.url, { waitUntil: "domcontentloaded", timeout: 60000 });
		// Some surfaces (voices, GPU adapter) populate asynchronously after load.
		await page.waitForTimeout(1500);
		const dump = await page.evaluate(DUMP, OPTS);

		const meta = {
			label,
			mode,
			capturedBy: "capture-fingerprint.mjs",
			request: org.seen.filter((r) => r.url === "/"),
		};
		const result = { __meta: meta, ...dump };
		const json = `${JSON.stringify(result, null, "\t")}\n`;
		if (outPath) {
			fs.mkdirSync(path.dirname(outPath), { recursive: true });
			fs.writeFileSync(outPath, json);
			console.error(`wrote ${outPath} (${json.length} bytes)`);
		} else {
			process.stdout.write(json);
		}
		if (has("--summary")) {
			console.error(`  UA        ${dump.navigator?.userAgent}`);
			console.error(`  platform  ${dump.navigator?.platform}`);
			console.error(`  screen    ${dump.screen?.width}x${dump.screen?.height} @${dump.screen?.devicePixelRatio}`);
			console.error(`  gl        ${dump.webgl1?.unmaskedRenderer}`);
			console.error(`  fonts     ${dump.fonts?.count}`);
		}
	} finally {
		await closeBrowser();
		org.close();
	}
}

run().catch((e) => { console.error(e); process.exit(1); });
