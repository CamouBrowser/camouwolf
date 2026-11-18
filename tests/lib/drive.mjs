/**
 * Shared harness for the browser-driving probes in tests/compare/.
 *
 * Every probe needs the same three things: a built binary, a Camouwolf launch with a
 * known identity, and a page it can evaluate JS in. Centralising it means a
 * probe is a single function, and — more importantly — that every probe drives
 * the browser the *same* way, so a difference between two probes' results is
 * about the browser and not about how they launched it.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

/**
 * Locate the binary under test.
 *
 * Never falls back to a `~/.cache/camouwolf` production install: a probe that
 * silently tests the previous release instead of the build you just made is the
 * single most expensive failure mode a harness has (it reports green for code
 * that was never run).
 */
export function resolveExecutable() {
	const explicit = process.env.CAMOUWOLF_EXECUTABLE_PATH;
	if (explicit) {
		if (!fs.existsSync(explicit)) {
			throw new Error(`CAMOUWOLF_EXECUTABLE_PATH does not exist: ${explicit}`);
		}
		return explicit;
	}
	const local = path.join(REPO, "browser-cache", "camouwolf");
	if (fs.existsSync(local)) return local;
	throw new Error(
		"No Camouwolf binary. Set CAMOUWOLF_EXECUTABLE_PATH, or extract a build into " +
			"browser-cache/. Probes deliberately do NOT fall back to a production " +
			"install — testing the wrong binary is worse than not testing.",
	);
}

/**
 * Serve one blank page on loopback, so probes get a REAL origin.
 *
 * A fresh `newPage()` sits on about:blank, which has a null origin and is not a
 * secure context — so `crypto.subtle`, `crypto.randomUUID`, and everything else
 * gated on secure context are `undefined` there. Stock Chrome behaves exactly
 * the same way, so a probe that reads them from about:blank is not measuring
 * the build, it is measuring its own harness (this cost a full suite run: the
 * whole stealth-surface probe died on `crypto.subtle.digest` of undefined).
 *
 * 127.0.0.1 is treated as potentially trustworthy, so this gets a secure
 * context without needing a certificate.
 */
async function loopbackOrigin() {
	const http = await import("node:http");
	const server = http.createServer((_req, res) => {
		res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
		res.end("<!doctype html><html><head><title>camouwolf-probe</title></head><body></body></html>");
	});
	await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
	const { port } = server.address();
	return { url: `http://127.0.0.1:${port}/`, close: () => server.close() };
}

/**
 * Launch Camouwolf, hand `fn` a page on a real origin, and tear everything down.
 *
 * `launch` is spread into the Camouwolf launch options, so a probe can pin the
 * identity it needs (`{ os: "windows", seed: 1 }`) and get a reproducible run.
 * Pass `blankPage: true` to stay on about:blank instead — only correct for a
 * probe that is deliberately testing the initial-page surface.
 */
export async function withPage(fn, launch = {}) {
	const { blankPage = false, ...launchOptions } = launch;
	const { Camouwolf } = await import(path.join(REPO, "dist/index.js"));
	const browser = await Camouwolf({
		// Real GPU when the box has one. "virtual" is Xvfb + llvmpipe, and a
		// software rasterizer is its own tell: sundial's OffscreenCanvas WebGL
		// renderer check times out under it, and several headless-shaped
		// signals (ActiveText resolving to pure red, viewport == screen) only
		// go away with a real display. CAMOUWOLF_GPU is what the launcher needs to
		// pick between multiple cards, so its presence is also the signal that
		// hardware rendering is available here.
		headless: process.env.CAMOUWOLF_HEADFUL
			? false
			: process.env.CAMOUWOLF_HEADLESS_MODE ||
				(process.env.CAMOUWOLF_GPU ? "virtual-gpu" : "virtual"),
		executable_path: resolveExecutable(),
		gpu: process.env.CAMOUWOLF_GPU,
		...launchOptions,
	});
	const origin = blankPage ? null : await loopbackOrigin();
	try {
		const page = await browser.newPage();

		// Camouwolf() hands back a Playwright Browser, so browser.newPage() creates a
		// DEFAULT context and never sees fp.contextOptions — the identity itself
		// still applies (it rides CAMOU_CONFIG into the browser process), but the
		// viewport does not, leaving Playwright's 1280x720 on every launch. When
		// the drawn persona happens to have a 1280x720 screen the two collide and
		// innerWidth/innerHeight equal screen.width/height exactly, which sundial
		// reads as headless. Apply the same formula NewContext uses so the
		// viewport belongs to the identity rather than to Playwright.
		//
		// This belongs in the launcher, not here: every consumer calling
		// browser.newPage() has the same hole.
		const vp = await page.evaluate(() => [
			screen.width,
			screen.height,
			devicePixelRatio,
		]);
		if (vp[0] && vp[1]) {
			// NOT page.setViewportSize(): that issues setDeviceMetricsOverride
			// with screenWidth/screenHeight equal to the viewport, so the
			// device-width/device-height media features follow the viewport and
			// stop matching the spoofed screen — trading "viewport == screen"
			// for "screen vs matchMedia incoherent". Drive the override directly
			// so the viewport shrinks while the device metrics keep reporting the
			// identity's screen.
			const cdp = await page.context().newCDPSession(page);
			await cdp.send("Emulation.setDeviceMetricsOverride", {
				width: vp[0],
				height: Math.max(vp[1] - 139, 600),
				deviceScaleFactor: vp[2] || 1,
				mobile: false,
				screenWidth: vp[0],
				screenHeight: vp[1],
			});
		}
		if (origin) await page.goto(origin.url, { waitUntil: "domcontentloaded" });
		return await fn(page, browser);
	} finally {
		await browser.close().catch(() => {});
		origin?.close();
	}
}

/** Evaluate `fn` in the page and return its result. Thin, but every probe uses it. */
export async function evalIn(page, fn, arg) {
	return page.evaluate(fn, arg);
}

/** Pretty-print a pass/fail line and track the exit status. */
export class Report {
	constructor(name) {
		this.name = name;
		this.failures = [];
		this.checks = 0;
	}

	check(ok, label, detail = "") {
		this.checks++;
		if (ok) {
			console.log(`  PASS  ${label}`);
			return true;
		}
		console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
		this.failures.push(label);
		return false;
	}

	finish() {
		const failed = this.failures.length;
		console.log(
			`\n${this.name}: ${this.checks - failed}/${this.checks} passed` +
				(failed ? ` — FAILED: ${this.failures.join(", ")}` : ""),
		);
		process.exitCode = failed ? 1 : 0;
		return failed === 0;
	}
}
