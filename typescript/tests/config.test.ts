/**
 * The config contract: the schema, validation, and how it reaches the
 * browser. Twin of python/tests/test_config.py.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
	InvalidPropertyType,
	KeyError,
	UnknownProperty,
} from "../src/exceptions.js";
import { LOCAL_DATA } from "../src/paths.js";
import { PyFloat } from "../src/pycompat.js";
import { restoreDeps, stubHost, utils } from "./launch-host.js";

const SCHEMA = path.join(LOCAL_DATA, "data", "properties.json");
const TYPES = ["str", "int", "uint", "double", "bool", "array", "dict"];

afterEach(() => {
	restoreDeps();
});

function scratchBundle(): string {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-config-"));
	const binary = path.join(dir, "camouwolf");
	fs.writeFileSync(binary, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
	return binary;
}

function join(env: Record<string, string>): [string, string[]] {
	const keys = Object.keys(env)
		.filter((k) => k.startsWith("CAMOU_CONFIG_"))
		.sort((a, b) => Number(a.split("_")[2]) - Number(b.split("_")[2]));
	return [keys.map((k) => env[k]).join(""), keys];
}

describe("the schema", () => {
	it("is a list of typed properties without repeats", () => {
		const entries: Array<Record<string, string>> = JSON.parse(
			fs.readFileSync(SCHEMA, "utf-8"),
		);
		const names = entries.map((entry) => entry.property);
		expect(new Set(names).size).toBe(names.length);
		for (const entry of entries) {
			expect(TYPES).toContain(entry.type);
			for (const key of Object.keys(entry))
				expect(["property", "type", "carriedBy", "items"]).toContain(key);
		}
		// What the launcher carries itself is marked, so the browser's copy can
		// drop it.
		expect(
			entries
				.filter((entry) => entry.carriedBy === "launcher")
				.map((entry) => entry.property)
				.sort(),
		).toEqual(
			[
				"window.outerWidth",
				"window.outerHeight",
				"window.screenX",
				"window.screenY",
				"geolocation:latitude",
				"geolocation:longitude",
				"geolocation:accuracy",
			].sort(),
		);
	});
});

describe("validateConfig", () => {
	it("throws UnknownProperty for an unknown key", () => {
		expect(() =>
			utils.validateConfig({ "navigator.oscpu": "Linux x86_64" }),
		).toThrow(/navigator\.oscpu/);
		expect(() =>
			utils.validateConfig({ "navigator.oscpu": "Linux x86_64" }),
		).toThrow(UnknownProperty);
		expect(new UnknownProperty("x")).toBeInstanceOf(KeyError);
	});

	it.each([
		["navigator.userAgent", 5],
		["navigator.hardwareConcurrency", -1],
		["navigator.hardwareConcurrency", "8"],
		["navigator.hardwareConcurrency", true],
		["navigator.deviceMemory", "8"],
		["screen.width", 1920.5],
		["userAgentData.mobile", 0],
		["navigator.languages", "en-US"],
		["navigator.languages", ["en-US", 5]],
		["fonts", { Arial: true }],
		["systemColors", ["canvas"]],
		["systemColors", { canvas: "#ffffff" }],
		["webGl:parameters", { MAX_TEXTURE_SIZE: 16384 }],
		["webGl:parameters", { "3379": [16384] }],
		["webGl2:parameters", { "3386": [1, 2, 3, 4, 5] }],
		["webGl2:parameters", { "3386": [true, false] }],
		["userAgentData.brands", ["Chromium"]],
		["userAgentData.brands", [{ brand: "Chromium" }]],
		["userAgentData.brands", [{ brand: "Chromium", version: 152 }]],
		["userAgentData.fullVersionList", [{ brand: "a", version: "1", x: "y" }]],
	])("refuses %s = %j", (key, value) => {
		expect(() => utils.validateConfig({ [key]: value })).toThrow(
			InvalidPropertyType,
		);
	});

	it("passes values of the right type", () => {
		utils.validateConfig({
			"navigator.userAgent": "x",
			"navigator.hardwareConcurrency": 8,
			"navigator.deviceMemory": 0.5,
			"screen.availLeft": -1920,
			"userAgentData.mobile": false,
			"navigator.languages": ["en-US"],
			systemColors: { canvas: 0xffffff },
			"webGl:parameters": { "3379": 16384, "7936": "WebKit" },
			"webGl2:parameters": { "3386": [32767, 32767], "33902": [1, 1] },
			"connection.downlink": new PyFloat(1.5),
			timezone: "Europe/Berlin",
			"userAgentData.brands": [{ brand: "Chromium", version: "152" }],
		});
	});

	it("validates a build against the schema it ships", () => {
		const bundle = scratchBundle();
		fs.writeFileSync(
			path.join(path.dirname(bundle), "properties.json"),
			JSON.stringify([{ property: "only.this", type: "str" }]),
		);
		utils.validateConfig({ "only.this": "x" }, bundle);
		expect(() =>
			utils.validateConfig({ "navigator.userAgent": "x" }, bundle),
		).toThrow(UnknownProperty);
	});
});

describe("the environment", () => {
	it("carries a small config in one variable", () => {
		const env = utils.configEnvVars({ "navigator.userAgent": "x" });
		expect(Object.keys(env)).toEqual(["CAMOU_CONFIG_1"]);
		expect(JSON.parse(env.CAMOU_CONFIG_1)).toEqual({
			"navigator.userAgent": "x",
		});
	});

	it.each([
		2047, 32767,
	])("splits a large config at %d and joins back", (chunkSize) => {
		const config = {
			fonts: Array.from({ length: 6000 }, (_, i) => `Font Family ${i}`),
			"navigator.userAgent": "é".repeat(50),
		};
		const env = utils.configEnvVars(config, chunkSize);
		const [blob, keys] = join(env);
		expect(keys.length).toBeGreaterThan(1);
		expect(keys).toEqual(keys.map((_, i) => `CAMOU_CONFIG_${i + 1}`));
		for (const key of keys)
			expect(Array.from(env[key]).length).toBeLessThanOrEqual(chunkSize);
		expect(JSON.parse(blob)).toEqual(config);
	});

	it("never splits a character outside the BMP", () => {
		// Python slices code points; a UTF-16 slice would cut a surrogate pair.
		const env = utils.configEnvVars({ fonts: ["\u{1F600}".repeat(40)] }, 7);
		for (const chunk of Object.values(env)) {
			expect(Array.from(chunk).length).toBeLessThanOrEqual(7);
			expect(chunk.isWellFormed()).toBe(true);
		}
		expect(JSON.parse(join(env)[0]).fonts[0]).toBe("\u{1F600}".repeat(40));
	});

	it("splits at 2047 on Windows", () => {
		stubHost("win");
		const env = utils.configEnvVars({
			fonts: Array(100).fill("x".repeat(100)),
		});
		expect(Math.max(...Object.values(env).map((v) => v.length))).toBe(2047);
	});

	it("refuses a config JSON cannot carry", () => {
		expect(() => utils.configEnvVars({ fonts: [() => 1] })).toThrow(
			InvalidPropertyType,
		);
	});

	it("asks for UCRT math for a Windows identity on another host", () => {
		stubHost("lin");
		expect(utils.getEnvVars({}, "win").CAMOUWOLF_UCRT_MATH).toBe("1");
		expect(utils.getEnvVars({}, "lin")).not.toHaveProperty(
			"CAMOUWOLF_UCRT_MATH",
		);
		stubHost("win");
		expect(utils.getEnvVars({}, "win")).not.toHaveProperty(
			"CAMOUWOLF_UCRT_MATH",
		);
	});

	it("makes the timezone the process zone too", () => {
		stubHost("lin");
		expect(utils.getEnvVars({ timezone: "Europe/Berlin" }, "lin").TZ).toBe(
			"Europe/Berlin",
		);
		expect(utils.getEnvVars({}, "lin")).not.toHaveProperty("TZ");
	});

	it("rewrites the bundle's fontconfig with absolute paths", () => {
		stubHost("lin");
		const bundle = scratchBundle();
		const root = path.dirname(bundle);
		fs.mkdirSync(path.join(root, "fontconfig", "windows"), { recursive: true });
		fs.writeFileSync(
			path.join(root, "fontconfig", "windows", "fonts.conf"),
			'<fontconfig><dir prefix="cwd">fonts</dir></fontconfig>',
		);
		// No font files yet: loading the config would leave the browser with none.
		expect(utils.getEnvVars({}, "win", bundle)).not.toHaveProperty(
			"FONTCONFIG_FILE",
		);
		fs.mkdirSync(path.join(root, "fonts", "windows"), { recursive: true });
		fs.mkdirSync(path.join(root, "fonts", "linux"), { recursive: true });
		fs.writeFileSync(path.join(root, "fonts", "windows", "arial.ttf"), "");
		const conf = utils.getEnvVars({}, "win", bundle).FONTCONFIG_FILE;
		const text = fs.readFileSync(conf, "utf-8");
		expect(text).toContain(`<dir>${path.join(root, "fonts", "windows")}</dir>`);
		expect(text).not.toContain('prefix="cwd"');
		// Never the parent: the other OS's fonts would become fallback candidates.
		expect(text).not.toContain(`<dir>${path.join(root, "fonts")}</dir>`);
		expect(conf.startsWith(root + path.sep)).toBe(false);
	});

	it("reads back the config a launch carries", () => {
		const env = utils.configEnvVars(
			{ "navigator.userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
			10,
		);
		expect(utils.launchConfig({ env })["navigator.userAgent"]).toContain(
			"Windows NT",
		);
		expect(utils.launchOs({ env })).toBe("windows");
		expect(utils.launchConfig({ env: {} })).toEqual({});
	});
});

describe("launcher-only keys", () => {
	it("keys SOCKS5 credentials by the proxy's canonical host and port", () => {
		expect(utils.socks5ProxyKey("socks5://Proxy.Example:1081")).toBe(
			"proxy.example:1081",
		);
		expect(utils.socks5ProxyKey("socks5://127.0.0.1")).toBe("127.0.0.1:1080");
		expect(utils.socks5ProxyKey("socks5://[0:0::1]:9050")).toBe("[::1]:9050");
	});

	it("takes SOCKS5 credentials off the proxy Playwright sees", () => {
		const [proxy, credentials] = utils.splitSocks5Credentials({
			server: "socks5://proxy.example:1081",
			username: "user",
			password: "pass",
		});
		expect(proxy).toEqual({ server: "socks5://proxy.example:1081" });
		expect(credentials).toEqual({
			"proxy.example:1081": { username: "user", password: "pass" },
		});
		const http = {
			server: "http://proxy.example:8080",
			username: "u",
			password: "p",
		};
		expect(utils.splitSocks5Credentials(http)).toEqual([http, null]);
		expect(() =>
			utils.splitSocks5Credentials({
				server: "socks5://proxy.example",
				username: "u",
				password: "x".repeat(256),
			}),
		).toThrow("A SOCKS5 password is 1 to 255 bytes (RFC 1929)");
		expect(
			utils.maskedConfig({ "proxy:socks5Credentials": credentials })[
				"proxy:socks5Credentials"
			],
		).toEqual({ "proxy.example:1081": { username: "user", password: "***" } });
	});

	it("reads certificatePaths into base64 DER certificates", () => {
		const dir = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-certs-"));
		const der = Buffer.from([0x30, 0x82, 0x01, 0x0a]);
		const body = der.toString("base64");
		fs.writeFileSync(path.join(dir, "a.der"), der);
		fs.writeFileSync(
			path.join(dir, "b.pem"),
			`-----BEGIN CERTIFICATE-----\n${body}\n-----END CERTIFICATE-----\n`,
		);
		const config: Record<string, any> = {
			certificatePaths: [path.join(dir, "a.der"), path.join(dir, "b.pem")],
		};
		utils.readCertificatePaths(config);
		expect(config).toEqual({ certificates: [body, body] });
	});
});
