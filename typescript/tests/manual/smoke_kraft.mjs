/**
 * Smoke test: launch a Kraft build through the TypeScript Camouwolf launcher.
 * Twin of python/tests/manual/smoke_kraft.py; it checks the same things.
 *
 * Kraft is the Chromium fork Camouwolf's patches come from; its binary reads
 * the same config under KRAFT_CONFIG_* where a Camouwolf build reads
 * CAMOU_CONFIG_*. The launcher is run unchanged and its environment duplicated
 * under Kraft's names (here only, never in the launcher), so what is checked
 * is the launcher's output against a real browser.
 *
 *     pnpm smoke [--binary PATH] [--headless true|virtual] [--os windows|linux]
 *                [--no-tz-env] [--json]
 *
 * Runs the built package (dist/), so `pnpm smoke` builds first. Never opens a
 * window on the real desktop: DISPLAY and WAYLAND_DISPLAY are removed, and
 * --headless virtual runs on an Xvfb this script starts. Exits non-zero when a
 * check fails. Not part of the test suite.
 */
import * as os from "node:os";
import * as path from "node:path";
import { parseArgs } from "node:util";

// Nothing here may reach the real desktop.
delete process.env.DISPLAY;
delete process.env.WAYLAND_DISPLAY;
process.removeAllListeners("warning");
process.on("warning", () => undefined);

const { Camouwolf, launchOptions, PRODUCT, VirtualDisplay } = await import(
	"../../dist/index.js"
);
const { launchConfig } = await import("../../dist/utils.js");

const DEFAULT_BINARY = path.join(
	os.homedir(),
	"jobharvest/Kraft/browser-cache/kraft-152.0.7977.42-alpha.1/kraft",
);

// navigator.userAgentData and navigator.deviceMemory exist only in a secure
// context, which a data: URL is not; the same document is also served, by
// request interception, from an https origin.
const SECURE_URL = "https://smoke.camouwolf.test/";

const DOCUMENT =
	"<!doctype html><title>smoke</title>" +
	'<button id=b style="position:absolute;left:300px;top:200px;width:160px;height:60px" ' +
	'onclick="window.clicked=(window.clicked||0)+1">go</button>' +
	"<script>window.moves=0;window.trusted=null;" +
	'addEventListener("mousemove",()=>{window.moves++});' +
	'addEventListener("click",e=>{window.trusted=e.isTrusted})</script>';
const PAGE = `data:text/html,${DOCUMENT}`;

const PROBE = async () => {
	const out = {};
	out.userAgent = navigator.userAgent;
	out.platform = navigator.platform;
	out.webdriver = navigator.webdriver;
	out.hardwareConcurrency = navigator.hardwareConcurrency;
	out.deviceMemory = navigator.deviceMemory;
	out.languages = navigator.languages;
	out.language = navigator.language;
	out.intlLocale = Intl.DateTimeFormat().resolvedOptions().locale;
	out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
	out.uad = navigator.userAgentData
		? {
				platform: navigator.userAgentData.platform,
				mobile: navigator.userAgentData.mobile,
				brands: navigator.userAgentData.brands,
				high: await navigator.userAgentData.getHighEntropyValues([
					"architecture",
					"bitness",
					"model",
					"platformVersion",
					"uaFullVersion",
					"wow64",
					"fullVersionList",
				]),
			}
		: null;
	out.screen = {
		width: screen.width,
		height: screen.height,
		availWidth: screen.availWidth,
		availHeight: screen.availHeight,
		colorDepth: screen.colorDepth,
	};
	out.window = {
		outerWidth,
		outerHeight,
		innerWidth,
		innerHeight,
		screenX,
		screenY,
		devicePixelRatio,
	};
	const gl = document.createElement("canvas").getContext("webgl");
	if (gl) {
		const ext = gl.getExtension("WEBGL_debug_renderer_info");
		out.webgl = {
			vendor: ext && gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
			renderer: ext && gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
			maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
		};
	} else {
		out.webgl = null;
	}
	out.workerUA = await new Promise((resolve) => {
		try {
			const w = new Worker(
				URL.createObjectURL(
					new Blob([
						"postMessage([navigator.userAgent, navigator.platform, navigator.hardwareConcurrency])",
					]),
				),
			);
			w.onmessage = (e) => resolve(e.data);
			w.onerror = () => resolve("worker error");
			setTimeout(() => resolve("worker timeout"), 5000);
		} catch (e) {
			resolve(String(e));
		}
	});
	return out;
};

const { values: opts } = parseArgs({
	options: {
		binary: { type: "string", default: DEFAULT_BINARY },
		headless: { type: "string", default: "true" },
		os: { type: "string", default: "windows" },
		"no-tz-env": { type: "boolean", default: false },
		json: { type: "boolean", default: false },
	},
});
if (!["true", "virtual"].includes(opts.headless))
	throw new Error("--headless is true or virtual");
if (!["windows", "linux"].includes(opts.os))
	throw new Error("--os is windows or linux");

const results = [];
function check(name, ok, detail = "") {
	results.push([name, Boolean(ok)]);
	console.log(
		`${ok ? "PASS" : "FAIL"}  ${name}${detail && !ok ? `  -- ${detail}` : ""}`,
	);
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

let display = null;
let extra = {};
if (opts.headless === "virtual") {
	display = new VirtualDisplay(PRODUCT);
	extra = {
		virtual_display: await display.get(),
		virtual_display_size: display.size,
	};
}

try {
	const options = await launchOptions({
		executable_path: opts.binary,
		os: opts.os,
		headless: opts.headless === "true",
		locale: "en-US",
		config: { timezone: "America/Chicago" },
		exclude_addons: ["UBO"],
		i_know_what_im_doing: true,
		...extra,
	});
	// The Kraft binary's names for the same contract.
	for (const [key, value] of Object.entries(options.env)) {
		if (key.startsWith("CAMOU_CONFIG_"))
			options.env[`KRAFT_CONFIG_${key.slice("CAMOU_CONFIG_".length)}`] = value;
		if (key === "CAMOUWOLF_UCRT_MATH") options.env.KRAFT_UCRT_MATH = value;
	}
	if (opts["no-tz-env"]) delete options.env.TZ;
	const claimed = launchConfig(options);

	check(
		"identity is in the environment, not argv",
		!options.args.some((arg) => arg.includes("Mozilla/5.0") || arg.includes("CAMOU")),
	);

	const browser = await Camouwolf({ from_options: options, humanize: true });
	try {
		const page = await browser.newPage();
		await page.goto(PAGE);
		const onDataUrl = await page.evaluate(PROBE);
		check(
			"data: URL loads and reports the identity",
			onDataUrl.userAgent === claimed["navigator.userAgent"] &&
				onDataUrl.platform === claimed["navigator.platform"],
			onDataUrl.userAgent,
		);
		await page.route(SECURE_URL, (route) =>
			route.fulfill({ contentType: "text/html", body: DOCUMENT }),
		);
		await page.goto(SECURE_URL);
		const seen = await page.evaluate(PROBE);
		if (opts.json) console.log(JSON.stringify(seen, null, 2));

		check("navigator.userAgent", seen.userAgent === claimed["navigator.userAgent"], seen.userAgent);
		check("navigator.platform", seen.platform === claimed["navigator.platform"], seen.platform);
		check("navigator.webdriver is false", seen.webdriver === false, String(seen.webdriver));
		check(
			"navigator.hardwareConcurrency",
			seen.hardwareConcurrency === claimed["navigator.hardwareConcurrency"],
			String(seen.hardwareConcurrency),
		);
		check(
			"navigator.deviceMemory",
			seen.deviceMemory === claimed["navigator.deviceMemory"],
			String(seen.deviceMemory),
		);
		check(
			"worker sees the same identity",
			same(seen.workerUA, [
				claimed["navigator.userAgent"],
				claimed["navigator.platform"],
				claimed["navigator.hardwareConcurrency"],
			]),
			String(seen.workerUA),
		);

		const uad = seen.uad ?? {};
		const high = uad.high ?? {};
		check(
			"userAgentData.platform",
			uad.platform === claimed["userAgentData.platform"],
			String(uad.platform),
		);
		check("userAgentData.mobile", uad.mobile === claimed["userAgentData.mobile"]);
		for (const key of ["architecture", "bitness", "model", "platformVersion", "uaFullVersion", "wow64"]) {
			check(
				`userAgentData.${key}`,
				high[key] === claimed[`userAgentData.${key}`],
				`${JSON.stringify(high[key])} != ${JSON.stringify(claimed[`userAgentData.${key}`])}`,
			);
		}
		const milestone = claimed["userAgentData.uaFullVersion"].split(".")[0];
		const brands = Object.fromEntries((uad.brands ?? []).map((b) => [b.brand, b.version]));
		check(
			"userAgentData.brands carry the milestone",
			brands.Chromium === milestone,
			JSON.stringify(brands),
		);

		for (const key of ["width", "height", "availWidth", "availHeight", "colorDepth"]) {
			check(
				`screen.${key}`,
				seen.screen[key] === claimed[`screen.${key}`],
				`${seen.screen[key]} != ${claimed[`screen.${key}`]}`,
			);
		}
		check(
			"window.outerWidth/outerHeight (launcher: --window-size)",
			seen.window.outerWidth === claimed["window.outerWidth"] &&
				seen.window.outerHeight === claimed["window.outerHeight"],
			`${JSON.stringify(seen.window)} != ${claimed["window.outerWidth"]}x${claimed["window.outerHeight"]}`,
		);

		const webgl = seen.webgl ?? {};
		check("WebGL vendor", webgl.vendor === claimed["webGl:vendor"], String(webgl.vendor));
		check("WebGL renderer", webgl.renderer === claimed["webGl:renderer"], String(webgl.renderer));
		const maxTexture = claimed["webGl:parameters"]?.["3379"];
		if (maxTexture !== undefined && maxTexture !== null) {
			check(
				"WebGL MAX_TEXTURE_SIZE (webGl:parameters)",
				webgl.maxTextureSize === maxTexture,
				`${webgl.maxTextureSize} != ${maxTexture}`,
			);
		}

		check("timezone", seen.timezone === claimed.timezone, seen.timezone);
		check(
			"navigator.languages",
			same(seen.languages, claimed["navigator.languages"]),
			String(seen.languages),
		);
		check(
			"Intl locale (launcher: --lang)",
			seen.intlLocale === claimed["navigator.languages"][0],
			seen.intlLocale,
		);

		check(
			'"mw:" evaluate reaches the main world',
			(await page.evaluate("mw:document.title").catch((e) => String(e))) === "smoke",
		);

		await page.click("#b");
		const state = await page.evaluate(
			"({clicked: window.clicked, moves: window.moves, trusted: window.trusted})",
		);
		check("humanized click lands", state.clicked === 1, JSON.stringify(state));
		check("humanized click is trusted", state.trusted === true, JSON.stringify(state));
		check(
			"humanized click moves the pointer along a path",
			state.moves >= 3,
			JSON.stringify(state),
		);
	} finally {
		await browser.close();
	}
} finally {
	display?.kill();
}

const failed = results.filter(([, ok]) => !ok).map(([name]) => name);
console.log(
	`\n${results.length - failed.length}/${results.length} checks passed` +
		(failed.length ? `; failed: ${JSON.stringify(failed)}` : ""),
);
process.exit(failed.length ? 1 : 0);
