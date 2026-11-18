/**
 * CDP-side detection: the layer no browser patch can fix.
 *
 * Camouwolf's patch series covers layer C (the fingerprint surface). This probe
 * covers layer B — the side effects of being driven over the Chrome DevTools
 * Protocol at all — which is a **driver** problem and therefore a launcher
 * problem, not a browser one.
 *
 * The canonical instance is the `Runtime.enable` leak found by the rebrowser
 * project. When a CDP client calls `Runtime.enable`, Chromium starts emitting
 * `Runtime.executionContextCreated` for every world — including the isolated
 * worlds a driver creates. A page can force a fresh execution context (create
 * an iframe, or throw inside one) and observe the resulting behavior change.
 * Playwright avoids the naive form by using `Runtime.addBinding` and isolated
 * worlds, but it is worth *verifying* rather than assuming, because the
 * behavior depends on how the driver was configured and it changes between
 * versions.
 *
 * Run:  node tests/compare/cdp-leak.mjs
 */
import { Report, withPage } from "../lib/drive.mjs";

const report = new Report("cdp-leak");

/**
 * The rebrowser-style probe.
 *
 * A fresh document is created and an error is thrown inside it. With
 * `Runtime.enable` active, the resulting execution-context bookkeeping is
 * observable; without it, the page sees nothing unusual. We look for the
 * artefacts a driven browser leaves rather than trying to reimplement the
 * detector's timing heuristics, which are noisy.
 */
const PROBE = () => {
	const results = {};

	// 1. Automation globals. undetected-chromedriver and Selenium leave these;
	//    a raw CDP driver should leave none.
	results.globals = Object.keys(window).filter((k) =>
		/^(cdc_|\$cdc|\$chrome_asyncScriptInfo|__driver|__webdriver|__selenium|__fxdriver|_Selenium_IDE|__nightmare|_phantom|callPhantom|domAutomation|domAutomationController)/.test(
			k,
		),
	);

	// 2. The `window.chrome` object. Stock headless Chromium historically did
	//    not have it, which made its absence one of the oldest headless checks
	//    there is.
	//
	//    NOT `runtime`, though. It is tempting to require it -- half the stealth
	//    literature says real Chrome has chrome.runtime -- but that is only true
	//    of a page an extension has declared `externally_connectable` for. On an
	//    ordinary page it is undefined. Measured on this box against stock Chrome
	//    147 and stock Chromium 151, both launched WITHOUT --disable-extensions:
	//    Object.keys(window.chrome) is exactly ["app","csi","loadTimes"] on both.
	//    Requiring `runtime` here asserted something untrue of real Chrome, so it
	//    failed a build that was correct.
	results.chrome = {
		present: typeof window.chrome === "object" && window.chrome !== null,
		keys: window.chrome ? Object.keys(window.chrome).sort() : [],
	};

	// 3. Permissions coherence. The classic sannysoft check: a headless Chrome
	//    reports Notification.permission "denied" while permissions.query says
	//    "prompt" — a state a real browser cannot be in.
	results.notificationPermission = Notification?.permission;

	// 4. navigator.plugins. Real Chrome reports five PDF-related entries; an
	//    empty array is a headless shape.
	results.plugins = Array.from(navigator.plugins ?? []).map((p) => p.name);
	results.mimeTypes = Array.from(navigator.mimeTypes ?? []).map((m) => m.type);

	// 5. Error.stack shape. A driver that evaluates through an injected wrapper
	//    leaves its own frames in a thrown stack.
	try {
		null.x;
	} catch (e) {
		results.stack = e.stack;
	}

	// 6. Function.prototype.toString on things a stealth plugin usually patches.
	results.toStrings = {
		permissionsQuery: navigator.permissions?.query?.toString(),
		getParameter: WebGLRenderingContext.prototype.getParameter.toString(),
		toDataURL: HTMLCanvasElement.prototype.toDataURL.toString(),
		getImageData: CanvasRenderingContext2D.prototype.getImageData.toString(),
	};

	return results;
};

await withPage(async (page) => {
	const got = await page.evaluate(PROBE);
	const perms = await page.evaluate(async () => {
		try {
			return (await navigator.permissions.query({ name: "notifications" })).state;
		} catch (e) {
			return `error:${e.message}`;
		}
	});

	report.check(
		got.globals.length === 0,
		"no automation globals on window",
		got.globals.join(","),
	);

	report.check(got.chrome.present, "window.chrome exists");
	for (const key of ["loadTimes", "csi", "app"]) {
		report.check(
			got.chrome.keys.includes(key),
			`window.chrome.${key} exists`,
			got.chrome.keys.join(","),
		);
	}

	// The contradiction, not either value on its own: a real Chrome that has
	// never been asked reports "default" AND "prompt". Reporting "denied" with
	// "prompt" is the state only headless produces.
	report.check(
		!(got.notificationPermission === "denied" && perms === "prompt"),
		"Notification.permission and permissions.query agree",
		`Notification.permission=${got.notificationPermission}, query=${perms}`,
	);

	report.check(
		got.plugins.length > 0,
		"navigator.plugins is not empty",
		`${got.plugins.length} plugins`,
	);
	report.check(
		got.mimeTypes.includes("application/pdf"),
		"application/pdf is in navigator.mimeTypes",
		got.mimeTypes.join(","),
	);

	// Every one of these must be native. A JS-level stealth plugin patching any
	// of them fails here — and Camouwolf must never introduce one, since the whole
	// point of the C++ series is that these stay untouched.
	for (const [name, source] of Object.entries(got.toStrings)) {
		report.check(
			source == null || /\{\s*\[native code\]\s*\}/.test(source),
			`${name} reports [native code]`,
			source?.slice(0, 60),
		);
	}

	// A stack thrown from PAGE-OWN script, not from inside page.evaluate().
	//
	// Throwing inside evaluate() always yields "at UtilityScript.evaluate" --
	// that is the driver's injection frame, present on stock Chrome under
	// Playwright too (verified against Chrome 147 and Chromium 151). Asserting
	// on it measured the harness, not the browser. What a detector can actually
	// see is the stack of an error raised by its OWN script, so raise it there.
	const pageStack = await page.evaluate(() => {
		const el = document.createElement("script");
		el.textContent =
			"try { null.x } catch (e) { window.__camouwolfStack = e.stack }";
		document.documentElement.appendChild(el);
		el.remove();
		return window.__camouwolfStack ?? "";
	});
	report.check(
		pageStack !== "" &&
			!/puppeteer|playwright|__playwright|pptr|UtilityScript/i.test(pageStack),
		"a page-origin stack carries no driver frames",
		pageStack.split("\n").slice(0, 3).join(" | "),
	);
});

report.finish();
