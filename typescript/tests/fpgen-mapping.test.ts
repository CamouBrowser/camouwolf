/**
 * An fpgen Chrome fingerprint becomes the config the browser reads.
 * Twin of python/tests/test_fpgen_mapping.py.
 */
import { describe, expect, it } from "vitest";
import { MODEL } from "./fpgen-setup.js";
import { fingerprints, utils } from "./launch-host.js";

const webgl = await import("../src/webgl.js");
const { fromFpgen } = fingerprints;

const RTX =
	"ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)";
const M2 = "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)";
const MESA =
	"ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)";

const WINDOWS_FINGERPRINT = {
	navigator: {
		userAgent:
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
		platform: "Win32",
		hardwareConcurrency: 12,
		deviceMemory: 8,
		oscpu: "undefined",
	},
	screen: {
		width: 1920,
		height: 1080,
		availWidth: 1920,
		availHeight: 1040,
		availLeft: 0,
		availTop: 0,
		colorDepth: 24,
	},
	window: {
		outerWidth: 1936,
		outerHeight: 1056,
		screenX: -8,
		screenY: -8,
		devicePixelRatio: 1,
	},
	devices: { audioinput: 1, videoinput: 1, audiooutput: 2 },
	gpu: { vendor: "Google Inc. (NVIDIA)", renderer: RTX },
	webgl: {
		params: {
			"3379": { name: "MAX_TEXTURE_SIZE", type: "Number", value: 16384 },
			"34076": {
				name: "MAX_CUBE_MAP_TEXTURE_SIZE",
				type: "Number",
				value: 16384,
			},
			"34047": {
				name: "MAX_TEXTURE_MAX_ANISOTROPY_EXT",
				type: "Number",
				value: 16,
			},
			"3386": {
				name: "MAX_VIEWPORT_DIMS",
				type: "Int32Array",
				value: [32767, 32767],
			},
			"7938": {
				name: "VERSION",
				type: "String",
				value: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
			},
			"2884": { name: "CULL_FACE", type: "Boolean", value: false },
			"34930": { name: "MAX_TEXTURE_IMAGE_UNITS", type: "Number", value: 16 },
		},
	},
	webgl2: {
		params: {
			"3379": { name: "MAX_TEXTURE_SIZE", type: "Number", value: 16384 },
			"34076": {
				name: "MAX_CUBE_MAP_TEXTURE_SIZE",
				type: "Number",
				value: 16384,
			},
			"34930": { name: "MAX_TEXTURE_IMAGE_UNITS", type: "Number", value: 32 },
		},
	},
	gpuInfo: {
		adapter: {
			default: {
				info: { vendor: "nvidia", architecture: "ampere", device: "" },
			},
		},
	},
	allFonts: ["Arial", "Calibri", "Segoe UI"],
	voices: [
		{
			default: true,
			lang: "en-US",
			localService: true,
			name: "Microsoft David - English (United States)",
			voiceURI: "Microsoft David - English (United States)",
		},
	],
};

describe("fromFpgen", () => {
	it("maps the keys and their values", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		expect(config["navigator.platform"]).toBe("Win32");
		expect(config["navigator.hardwareConcurrency"]).toBe(12);
		expect(config["navigator.deviceMemory"]).toBe(8);
		expect(config["screen.width"]).toBe(1920);
		expect(config["screen.availHeight"]).toBe(1040);
		expect(config["screen.colorDepth"]).toBe(24);
		expect(config["window.outerWidth"]).toBe(1936);
		expect(config["window.outerHeight"]).toBe(1056);
		expect(config["webGl:vendor"]).toBe("Google Inc. (NVIDIA)");
		expect(config["webGl:renderer"]).toContain("RTX 3060");
		expect([
			config["webGpu:vendor"],
			config["webGpu:architecture"],
			config["webGpu:device"],
		]).toEqual(["nvidia", "ampere", ""]);
	});

	it("rewrites the user agent to the running milestone", () => {
		const ua = fromFpgen(WINDOWS_FINGERPRINT, 152)["navigator.userAgent"];
		expect(ua).toContain("Chrome/152.0.0.0");
		expect(ua).not.toContain("Chrome/143");
		expect(fromFpgen(WINDOWS_FINGERPRINT)["navigator.userAgent"]).toContain(
			"Chrome/143.0.0.0",
		);
	});

	it("sends only the limits both context versions agree on", () => {
		// Dropped: an extension limit, an array, a string, state, and a limit
		// WebGL2 reports differently.
		expect(fromFpgen(WINDOWS_FINGERPRINT, 152)["webGl:parameters"]).toEqual({
			"3379": 16384,
			"34076": 16384,
		});
	});

	it("gives fonts, voices and devices the browser's formats", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		expect(config.fonts).toEqual(
			expect.arrayContaining(["Arial", "Calibri", "Segoe UI"]),
		);
		expect(config.voices).toEqual([
			"Microsoft David - English (United States)|en-US|Microsoft David - English (United States)|1|1",
		]);
		const entries: string[] = config["mediaDevices:enumerated"];
		// Without media permission Chrome lists one unlabelled device per kind,
		// however many the machine has.
		expect(entries.map((entry) => entry.split("|")[0])).toEqual([
			"audioinput",
			"videoinput",
			"audiooutput",
		]);
		for (const entry of entries) expect(entry.endsWith("|||")).toBe(true);
	});

	it.each([
		"win",
		"mac",
		"lin",
	] as const)("does not claim a font the %s bundle cannot render", (os) => {
		// fpgen records name families no bundled OS ships; each one
		// measures as missing in the page.
		const claimed = fingerprints.fontList(
			["Abyssinica SIL", "Arial", "arial"],
			os,
		) as string[];
		expect(claimed).not.toContain("Abyssinica SIL");
		expect(claimed.filter((f) => f.toLowerCase() === "arial")).toHaveLength(1);
	});

	it.each([
		"win",
		"mac",
		"lin",
	] as const)("claims only essential fonts the %s bundle carries", (os) => {
		const claimed = fingerprints.fontList(["Abyssinica SIL"], os);
		expect(claimed).toEqual(fingerprints.ESSENTIAL_FONTS[os]);
	});

	it("maps only keys in the schema", () => {
		utils.validateConfig(fromFpgen(WINDOWS_FINGERPRINT, 152));
	});

	it("does not send keys the browser cannot read", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		expect(config).not.toHaveProperty(["navigator.oscpu"]);
	});

	it("sends the device pixel ratio, which the browser renders at", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		expect(config["window.devicePixelRatio"]).toBe(
			WINDOWS_FINGERPRINT.window.devicePixelRatio,
		);
	});

	it("maps touch points and pixel depth", () => {
		const config = fromFpgen(
			{
				...WINDOWS_FINGERPRINT,
				navigator: { ...WINDOWS_FINGERPRINT.navigator, maxTouchPoints: 0 },
				screen: { ...WINDOWS_FINGERPRINT.screen, pixelDepth: 24 },
			},
			152,
		);
		expect(config["navigator.maxTouchPoints"]).toBe(0);
		expect(config["screen.pixelDepth"]).toBe(24);
		utils.validateConfig(config);
	});
});

describe("webgl", () => {
	it.each([
		[RTX, "win", true],
		[RTX, "lin", false],
		[M2, "mac", true],
		[M2, "win", false],
		[MESA, "lin", true],
		[MESA, "windows", false],
	])("%s on %s: %s", (renderer, target, ok) => {
		expect(webgl.backendMatchesOs(renderer, target)).toBe(ok);
	});

	it("does not let an identity claim a software renderer", () => {
		expect(
			webgl.usableGpu({
				vendor: "Google Inc. (Google)",
				renderer:
					"ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)",
			}),
		).toBe(false);
		expect(webgl.usableGpu(null)).toBe(false);
		expect(webgl.usableGpu({ vendor: "", renderer: "" })).toBe(false);
		expect(webgl.usableGpu(WINDOWS_FINGERPRINT.gpu, "win")).toBe(true);
		expect(webgl.usableGpu(WINDOWS_FINGERPRINT.gpu, "lin")).toBe(false);
	});
});

describe.skipIf(!MODEL.ok)("the fpgen model", () => {
	it.each([
		["windows", "win"],
		["linux", "lin"],
		["macos", "mac"],
	])("draws Chrome on %s", (osName, key) => {
		const fingerprint = fingerprints.generateFingerprint({ os: osName });
		expect(fingerprint.navigator.userAgent).toContain("Chrome/");
		expect(fingerprints.fingerprintOs(fingerprint)).toBe(key);
		utils.validateConfig(fromFpgen(fingerprint, 152));
	});

	it("records hardware GPUs on the OS's backend", () => {
		const gpus = webgl.chromeGpus("windows");
		expect(gpus.length).toBeGreaterThan(0);
		for (const [, renderer] of gpus)
			expect(webgl.backendMatchesOs(renderer, "win")).toBe(true);
	});
});
