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
		renderer: RTX,
		params: {
			"3379": { name: "MAX_TEXTURE_SIZE", type: "Number", value: 16384 },
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
			"33902": {
				name: "ALIASED_LINE_WIDTH_RANGE",
				type: "Float32Array",
				value: [1, 1],
			},
			"3408": { name: "SUBPIXEL_BITS", type: "Number", value: 4 },
			"7938": {
				name: "VERSION",
				type: "String",
				value: "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
			},
			"2884": { name: "CULL_FACE", type: "Boolean", value: false },
			"2978": {
				name: "VIEWPORT",
				type: "Int32Array",
				value: [0, 0, 300, 150],
			},
			"34930": { name: "MAX_TEXTURE_IMAGE_UNITS", type: "Number", value: 16 },
		},
		supportedExtensions: ["ANGLE_instanced_arrays", "WEBGL_draw_buffers"],
		shaderPrecisionFormats: [
			{
				shaderType: 35633,
				precisionType: 36337,
				shaderPrecisionFormat: { rangeMin: 127, rangeMax: 127, precision: 23 },
			},
		],
	},
	webgl2: {
		renderer: RTX,
		params: {
			"3379": { name: "MAX_TEXTURE_SIZE", type: "Number", value: 16384 },
			"34930": { name: "MAX_TEXTURE_IMAGE_UNITS", type: "Number", value: 32 },
			"35076": { name: "MIN_PROGRAM_TEXEL_OFFSET", type: "Number", value: -8 },
			"37447": {
				name: "MAX_CLIENT_WAIT_TIMEOUT_WEBGL",
				type: "Number",
				value: 0,
			},
		},
		supportedExtensions: ["OVR_multiview2"],
		shaderPrecisionFormats: [],
	},
	gpuInfo: {
		unmaskedRenderer: RTX,
		preferredCanvasFormat: "bgra8unorm",
		adapter: {
			default: {
				info: {
					vendor: "nvidia",
					architecture: "ampere",
					device: "",
					subgroupMinSize: 32,
					subgroupMaxSize: 128,
				},
				features: ["shader-f16", "texture-compression-bc"],
				limits: {
					maxBufferSize: 2147483648,
					maxComputeWorkgroupStorageSize: 32768,
				},
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

	it("claims each context version's own device limits", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		// Dropped: an extension limit, a string, state (CULL_FACE, VIEWPORT) and
		// a WebGL2 limit named like an extension's.
		expect(config["webGl:parameters"]).toEqual({
			"3379": 16384,
			"3386": [32767, 32767],
			"33902": [1, 1],
			"3408": 4,
			"34930": 16,
		});
		expect(config["webGl2:parameters"]).toEqual({
			"3379": 16384,
			"34930": 32,
			"35076": -8,
		});
		expect(config["webGl:supportedExtensions"]).toEqual([
			"ANGLE_instanced_arrays",
			"WEBGL_draw_buffers",
		]);
		expect(config["webGl2:supportedExtensions"]).toEqual(["OVR_multiview2"]);
		expect(config["webGl:shaderPrecisionFormats"]).toEqual({
			"35633,36337": { rangeMin: 127, rangeMax: 127, precision: 23 },
		});
		expect(config).not.toHaveProperty(["webGl2:shaderPrecisionFormats"]);
	});

	it("claims no WebGL readout of another GPU", () => {
		// fpgen occasionally pairs one device's GPU with another's WebGL
		// readout; its limits would contradict the claimed renderer.
		const other = {
			...WINDOWS_FINGERPRINT,
			webgl2: {
				...WINDOWS_FINGERPRINT.webgl2,
				renderer: "ANGLE (Intel, other)",
			},
		};
		const keys = Object.keys(fromFpgen(other, 152));
		expect(keys.filter((k) => /^(webGl:[ps]|webGl2:)/.test(k))).toEqual([]);
	});

	it("claims the WebGPU adapter whole", () => {
		const config = fromFpgen(WINDOWS_FINGERPRINT, 152);
		expect(config["webGpu:preferredCanvasFormat"]).toBe("bgra8unorm");
		expect([
			config["webGpu:subgroupMinSize"],
			config["webGpu:subgroupMaxSize"],
		]).toEqual([32, 128]);
		expect(config["webGpu:features"]).toEqual([
			"shader-f16",
			"texture-compression-bc",
		]);
		expect(config["webGpu:limits"]).toEqual({
			maxBufferSize: 2147483648,
			maxComputeWorkgroupStorageSize: 32768,
		});
	});

	it("claims no adapter of another GPU, but the OS's canvas format", () => {
		const other = {
			...WINDOWS_FINGERPRINT,
			gpuInfo: {
				...WINDOWS_FINGERPRINT.gpuInfo,
				unmaskedRenderer: "ANGLE (Intel, other)",
			},
		};
		const config = fromFpgen(other, 152);
		expect(config["webGpu:preferredCanvasFormat"]).toBe("bgra8unorm");
		expect(
			Object.keys(config).filter(
				(k) => k.startsWith("webGpu:") && k !== "webGpu:preferredCanvasFormat",
			),
		).toEqual([]);
	});

	it("keeps the browser's canvas format for a Linux identity", () => {
		expect(
			fingerprints.webgpuAdapter(WINDOWS_FINGERPRINT.gpuInfo, RTX, "lin"),
		).not.toHaveProperty(["webGpu:preferredCanvasFormat"]);
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

const PREFER_HARDWARE = { hardwareAcceleration: "prefer-hardware" };
const PREFER_SOFTWARE = { hardwareAcceleration: "prefer-software" };

function mediaRecord(userAgent: string) {
	const hardware = ["basic", PREFER_HARDWARE, PREFER_SOFTWARE];
	const software = ["basic", PREFER_SOFTWARE];
	return {
		...WINDOWS_FINGERPRINT,
		navigator: { ...WINDOWS_FINGERPRINT.navigator, userAgent },
		mediaDecoderSupport: {
			video: {
				"avc1.42E01E": hardware,
				"vp09.00.10.08": hardware,
				"hvc1.1.6.L93.B0": hardware,
				vp8: software,
				"av01.0.04M.08": software,
				// A config that names more than the preference is not the plain
				// prefer-hardware probe.
				"avc3.640028": [
					{ codedWidth: 3840, codedHeight: 2160, ...PREFER_HARDWARE },
				],
			},
		},
		rtcCapabilities: {
			video: {
				codecs: [
					{ mimeType: "video/VP8" },
					{
						mimeType: "video/H264",
						sdpFmtpLine:
							"level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
					},
					{
						mimeType: "video/H264",
						sdpFmtpLine:
							"level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640020",
					},
					{
						mimeType: "video/H265",
						sdpFmtpLine: "level-id=123;profile-id=1;tier-flag=0;tx-mode=SRST",
					},
				],
			},
		},
	};
}

describe("platformCodecs", () => {
	it("claims the record's hardware codecs", () => {
		const config = fromFpgen(
			mediaRecord(WINDOWS_FINGERPRINT.navigator.userAgent),
		);
		expect(config["media:platformDecoders"]).toEqual(["h264", "hevc", "vp9"]);
		expect(config["media:platformEncoders"]).toEqual(["aac", "h264", "hevc"]);
		expect(config["media:rtcEncodeFormats"]).toEqual([
			"H264|level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640020",
			"H265|level-id=123;profile-id=1;tier-flag=0;tx-mode=SRST",
		]);
		expect(config["media:rtcDecodeFormats"]).toEqual([
			...fingerprints.RTC_DECODE_FORMATS.h264,
			...fingerprints.RTC_DECODE_FORMATS.hevc,
		]);
	});

	it("encodes no AAC on Linux, and claims nothing without the data", () => {
		const linux = mediaRecord(
			"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
		);
		expect(fromFpgen(linux)["media:platformEncoders"]).not.toContain("aac");
		expect(
			Object.keys(fromFpgen(WINDOWS_FINGERPRINT)).filter((key) =>
				key.startsWith("media:"),
			),
		).toEqual([]);
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
