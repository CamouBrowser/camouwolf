/**
 * Read every spoofed surface out of a live Camouwolf and check it against the
 * identity the launcher claimed.
 *
 * This is the probe that catches the failure mode the whole project is built
 * around: a config key the launcher writes that NO patch reads. Nothing else
 * notices — the browser starts, the launch reports success, and the surface is
 * simply stock. Here, the claim and the reading are compared directly.
 *
 * Run:  node tests/compare/stealth-surface.mjs [--os windows] [--seed 1]
 */
import { Report, withPage } from "../lib/drive.mjs";

const argv = process.argv.slice(2);
const arg = (n, d) => {
	const i = argv.indexOf(n);
	return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const os = arg("--os", "windows");
const seed = Number(arg("--seed", "1"));

/** Everything a page can read, in one evaluate. */
const READ_ALL = async () => {
	const canvas = document.createElement("canvas");
	canvas.width = 64;
	canvas.height = 32;
	const ctx2d = canvas.getContext("2d");
	ctx2d.textBaseline = "top";
	ctx2d.font = "14px 'Arial'";
	ctx2d.fillStyle = "#f60";
	ctx2d.fillRect(0, 0, 62, 20);
	ctx2d.fillStyle = "#069";
	ctx2d.fillText("Camouwolf,\u{1F600}", 2, 2);

	const hash = async (buf) => {
		const digest = await crypto.subtle.digest("SHA-256", buf);
		return [...new Uint8Array(digest)]
			.map((b) => b.toString(16).padStart(2, "0"))
			.join("")
			.slice(0, 16);
	};

	const imageData = ctx2d.getImageData(0, 0, 64, 32);
	const canvasHashA = await hash(imageData.data.buffer);
	const canvasHashB = await hash(
		ctx2d.getImageData(0, 0, 64, 32).data.buffer,
	);
	const dataUrl = canvas.toDataURL();

	const gl = document.createElement("canvas").getContext("webgl2");
	const dbg = gl?.getExtension("WEBGL_debug_renderer_info");

	let voices = [];
	try {
		voices = speechSynthesis.getVoices().map((v) => ({
			name: v.name,
			lang: v.lang,
			uri: v.voiceURI,
			local: v.localService,
			def: v.default,
		}));
	} catch {}

	let highEntropy = {};
	try {
		highEntropy = await navigator.userAgentData.getHighEntropyValues([
			"architecture",
			"bitness",
			"model",
			"platformVersion",
			"uaFullVersion",
			"fullVersionList",
			"wow64",
		]);
	} catch {}

	// The same reads, taken again from a fresh realm. A JS-level patch that
	// applies to the main window but not to a same-origin iframe fails here —
	// which is the whole reason the spoofing is in C++.
	const frame = document.createElement("iframe");
	document.body.appendChild(frame);
	const inFrame = {
		webdriver: frame.contentWindow.navigator.webdriver,
		platform: frame.contentWindow.navigator.platform,
		hardwareConcurrency: frame.contentWindow.navigator.hardwareConcurrency,
		userAgent: frame.contentWindow.navigator.userAgent,
	};
	// And from a Worker, which is a different thread AND a different global.
	const inWorker = await new Promise((resolve) => {
		const src = `postMessage({platform:navigator.platform,` +
			`hardwareConcurrency:navigator.hardwareConcurrency,` +
			`userAgent:navigator.userAgent,` +
			`languages:navigator.languages.join(","),` +
			`tz:Intl.DateTimeFormat().resolvedOptions().timeZone});`;
		const w = new Worker(URL.createObjectURL(new Blob([src])));
		const timer = setTimeout(() => resolve({ error: "timeout" }), 5000);
		w.onmessage = (e) => {
			clearTimeout(timer);
			resolve(e.data);
		};
	});

	return {
		navigator: {
			userAgent: navigator.userAgent,
			platform: navigator.platform,
			vendor: navigator.vendor,
			hardwareConcurrency: navigator.hardwareConcurrency,
			deviceMemory: navigator.deviceMemory,
			languages: navigator.languages,
			maxTouchPoints: navigator.maxTouchPoints,
			webdriver: navigator.webdriver,
			// Frozen Chrome constants — a patch must not have moved these.
			appCodeName: navigator.appCodeName,
			appName: navigator.appName,
			product: navigator.product,
			productSub: navigator.productSub,
			oscpu: navigator.oscpu,
		},
		userAgentData: {
			brands: navigator.userAgentData?.brands,
			mobile: navigator.userAgentData?.mobile,
			platform: navigator.userAgentData?.platform,
			...highEntropy,
		},
		screen: {
			width: screen.width,
			height: screen.height,
			availWidth: screen.availWidth,
			availHeight: screen.availHeight,
			colorDepth: screen.colorDepth,
			pixelDepth: screen.pixelDepth,
			isExtended: screen.isExtended,
		},
		timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
		timezoneOffset: new Date().getTimezoneOffset(),
		webgl: {
			vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
			renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
			maxTextureSize: gl?.getParameter(gl.MAX_TEXTURE_SIZE),
			maxVertexAttribs: gl?.getParameter(gl.MAX_VERTEX_ATTRIBS),
		},
		canvasHashA,
		canvasHashB,
		dataUrlHash: await hash(new TextEncoder().encode(dataUrl)),
		textWidth: ctx2d.measureText("The quick brown fox").width,
		voices,
		inFrame,
		inWorker,
		// toString() of a patched getter must still look native. Any JS-level
		// override fails this, which is the point of doing it in C++.
		nativeToString: {
			webdriver: Object.getOwnPropertyDescriptor(
				Navigator.prototype,
				"webdriver",
			)?.get?.toString(),
			platform: Object.getOwnPropertyDescriptor(
				Navigator.prototype,
				"platform",
			)?.get?.toString(),
		},
		// Automation residue a stock automated Chrome carries.
		automationResidue: Object.keys(window).filter((k) =>
			/^(cdc_|\$cdc|__webdriver|__selenium|__nightmare|_phantom|callPhantom|domAutomation)/.test(
				k,
			),
		),
	};
};

const report = new Report(`stealth-surface (${os}, seed=${seed})`);

await withPage(
	async (page) => {
		// Read the identity the launcher claimed, straight out of the process env
		// it built — the same bytes the browser received.
		const { launchOptions } = await import("../../dist/utils.js");
		const built = await launchOptions({
			os,
			seed,
			stealth: true,
			headless: true,
			fingerprint_preset: true,
			executable_path: process.env.CAMOUWOLF_EXECUTABLE_PATH,
		});
		const env = built.env;
		const claimed = JSON.parse(
			Object.keys(env)
				.filter((k) => k.startsWith("CAMOU_CONFIG_"))
				.sort((a, b) => Number(a.split("_").pop()) - Number(b.split("_").pop()))
				.map((k) => env[k])
				.join(""),
		);

		const got = await page.evaluate(READ_ALL);

		// ---- automation ---------------------------------------------------
		report.check(got.navigator.webdriver === false, "navigator.webdriver is false");
		report.check(
			got.inFrame.webdriver === false,
			"navigator.webdriver is false in an iframe",
		);
		report.check(
			got.automationResidue.length === 0,
			"no cdc_/webdriver globals",
			got.automationResidue.join(","),
		);
		report.check(
			/\[native code\]/.test(got.nativeToString.webdriver ?? ""),
			"webdriver getter reports [native code]",
		);
		report.check(
			/\[native code\]/.test(got.nativeToString.platform ?? ""),
			"platform getter reports [native code]",
		);

		// ---- frozen Chrome constants ---------------------------------------
		report.check(got.navigator.appCodeName === "Mozilla", "appCodeName frozen");
		report.check(got.navigator.appName === "Netscape", "appName frozen");
		report.check(got.navigator.product === "Gecko", "product frozen");
		report.check(got.navigator.productSub === "20030107", "productSub frozen");
		report.check(
			got.navigator.oscpu === undefined,
			"navigator.oscpu absent (a Firefox-only property)",
			String(got.navigator.oscpu),
		);
		report.check(got.navigator.vendor === "Google Inc.", "vendor is Google Inc.");

		// ---- identity matches the claim -------------------------------------
		for (const [key, actual] of [
			["navigator.userAgent", got.navigator.userAgent],
			["navigator.platform", got.navigator.platform],
			["navigator.hardwareConcurrency", got.navigator.hardwareConcurrency],
			["navigator.deviceMemory", got.navigator.deviceMemory],
			["screen.width", got.screen.width],
			["screen.height", got.screen.height],
			["screen.availWidth", got.screen.availWidth],
			["screen.availHeight", got.screen.availHeight],
			["screen.colorDepth", got.screen.colorDepth],
			["webGl:vendor", got.webgl.vendor],
			["webGl:renderer", got.webgl.renderer],
			["userAgentData.platform", got.userAgentData.platform],
			["userAgentData.platformVersion", got.userAgentData.platformVersion],
			["userAgentData.architecture", got.userAgentData.architecture],
			["userAgentData.bitness", got.userAgentData.bitness],
		]) {
			if (claimed[key] === undefined) continue;
			report.check(
				String(actual) === String(claimed[key]),
				`${key} matches the claim`,
				`claimed ${claimed[key]}, read ${actual}`,
			);
		}

		// ---- realm invariance ------------------------------------------------
		report.check(
			got.inFrame.platform === got.navigator.platform,
			"platform is identical in an iframe",
		);
		report.check(
			got.inWorker.platform === got.navigator.platform,
			"platform is identical in a Worker",
			`main ${got.navigator.platform}, worker ${got.inWorker.platform}`,
		);
		report.check(
			got.inWorker.hardwareConcurrency === got.navigator.hardwareConcurrency,
			"hardwareConcurrency is identical in a Worker",
		);
		report.check(
			got.inWorker.tz === got.timezone,
			"timezone is identical in a Worker",
			`main ${got.timezone}, worker ${got.inWorker.tz}`,
		);

		// ---- headless tells ---------------------------------------------------
		report.check(
			!(
				got.screen.width === got.screen.availWidth &&
				got.screen.height === got.screen.availHeight
			),
			"screen has a taskbar inset (CreepJS noTaskbar)",
		);
		report.check(
			!/SwiftShader|llvmpipe|Google SwiftShader/i.test(got.webgl.renderer ?? ""),
			"WebGL renderer is not a software rasterizer",
			got.webgl.renderer,
		);
		report.check(
			!/HeadlessChrome/.test(got.navigator.userAgent),
			"UA does not say HeadlessChrome",
		);

		// ---- farbling ---------------------------------------------------------
		report.check(
			got.canvasHashA === got.canvasHashB,
			"canvas readback is STABLE across two reads",
			`${got.canvasHashA} vs ${got.canvasHashB}`,
		);
		report.check(
			Number.isFinite(got.textWidth) && got.textWidth > 0,
			"measureText returns a usable width",
		);
		report.check(
			Math.abs(got.textWidth * 64 - Math.round(got.textWidth * 64)) < 1e-9,
			"measureText width is on the 26.6 fixed-point grid",
			String(got.textWidth),
		);

		// ---- client hints -----------------------------------------------------
		const brands = got.userAgentData.brands ?? [];
		report.check(
			brands.some((b) => /Not.?A.?Brand/.test(b.brand)),
			"Sec-CH-UA carries a GREASE brand",
		);
		report.check(
			brands.some((b) => b.brand === "Google Chrome"),
			"Sec-CH-UA carries the Google Chrome brand",
		);
		report.check(got.userAgentData.mobile === false, "Sec-CH-UA-Mobile is ?0");
		report.check(got.userAgentData.model === "", "Sec-CH-UA-Model is empty on desktop");

		// ---- voices ------------------------------------------------------------
		if (got.voices.length) {
			report.check(
				got.voices.every((v) => v.uri === v.name),
				"voiceURI equals name (Chrome's shape, not Firefox's urn:moz-tts:)",
			);
			report.check(
				got.voices.some((v) => /^Google /.test(v.name) && !v.local),
				"the Google network voices a real Chrome ships are present",
			);
			report.check(
				got.voices.filter((v) => v.def).length === 1,
				"exactly one default voice",
			);
		}
	},
	{ os, seed, fingerprint_preset: true },
);

report.finish();
