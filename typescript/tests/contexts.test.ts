/**
 * Camouwolf()/NewBrowser()/NewContext() against a fake Playwright browser
 * type: what is launched, and the defaults every context gets. Twin of
 * python/tests/test_contexts.py and the launch-path tests of
 * test_launch_options.py that the goldens cannot hold.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	IncoherentIdentity,
	InvalidIP,
	NonChromeFingerprint,
	NotImplementedError,
	UnknownIPLocation,
	ValueError,
} from "../src/exceptions.js";
import { MODEL } from "./fpgen-setup.js";
import {
	BUNDLE_EXE,
	configOf,
	HOME,
	quietly,
	restoreDeps,
	stubHost,
	utils,
	warnings,
} from "./launch-host.js";

const { NewBrowser, NewContext } = await import("../src/sync_api.js");
const { VirtualDisplay } = await import("../src/virtdisplay.js");

class FakeBrowser {
	closed = false;
	calls: Array<[string, any]> = [];
	contexts() {
		return [];
	}
	on() {}
	async newContext(options?: any) {
		this.calls.push(["newContext", options]);
		return { pages: () => [], on() {} };
	}
	async newPage(options?: any) {
		this.calls.push(["newPage", options]);
		return {};
	}
	async close() {
		this.closed = true;
	}
}

class FakeBrowserType {
	launches: any[] = [];
	persistent: Array<[string, any]> = [];
	browser = new FakeBrowser();
	fail: Error | null = null;
	async launch(options: any) {
		if (this.fail) throw this.fail;
		this.launches.push(options);
		return this.browser;
	}
	async launchPersistentContext(userDataDir: string, options: any) {
		this.persistent.push([userDataDir, options]);
		return { pages: () => [], on() {}, close: async () => undefined };
	}
}

const base = () => ({
	os: "linux",
	headless: true,
	executable_path: BUNDLE_EXE,
	exclude_addons: ["UBO" as const],
	env: { HOME },
});

beforeEach(() => {
	fs.mkdirSync(path.dirname(BUNDLE_EXE), { recursive: true });
	if (!fs.existsSync(BUNDLE_EXE))
		fs.writeFileSync(BUNDLE_EXE, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
	stubHost("lin");
});

afterEach(() => {
	restoreDeps();
	vi.restoreAllMocks();
});

describe.skipIf(!MODEL.ok)("NewBrowser", () => {
	it("launches Chromium with the launch options", async () => {
		const type = new FakeBrowserType();
		const browser = await NewBrowser(type as any, base());
		expect(browser).toBe(type.browser);
		const [launched] = type.launches;
		expect(launched.executablePath).toBe(BUNDLE_EXE);
		expect(launched.headless).toBe(true);
		expect(Object.keys(launched.env)).toContain("CAMOU_CONFIG_1");
		expect(launched).not.toHaveProperty("firefoxUserPrefs");
	});

	it("accepts the Playwright object as Python does", async () => {
		const type = new FakeBrowserType();
		await NewBrowser({ chromium: type } as any, base());
		expect(type.launches).toHaveLength(1);
	});

	it("launches from_options as given", async () => {
		const type = new FakeBrowserType();
		const options = await utils.launchOptions(base());
		await NewBrowser(type as any, { from_options: options });
		expect(type.launches).toEqual([options]);
	});

	it("gives every context the real window and the host's media features", async () => {
		const type = new FakeBrowserType();
		const browser = await NewBrowser(type as any, base());
		await browser.newContext();
		await browser.newPage({ viewport: { width: 800, height: 600 } });
		await browser.newContext({ colorScheme: "dark" });
		const [[, first], [, second], [, third]] = type.browser.calls;
		expect(first).toEqual({ viewport: null, ...utils.STOCK_MEDIA_DEFAULTS });
		// A caller's viewport wins.
		expect(second.viewport).toEqual({ width: 800, height: 600 });
		expect(third.colorScheme).toBe("dark");
		expect(third.reducedMotion).toBeNull();
	});

	it("grants the launch's geolocation to every context", async () => {
		const type = new FakeBrowserType();
		const browser = await NewBrowser(type as any, {
			...base(),
			geoip: "198.51.100.4",
		});
		await browser.newContext({ permissions: ["notifications"] });
		const [[, options]] = type.browser.calls;
		expect(options.geolocation).toEqual({
			latitude: 52.5244,
			longitude: 13.4105,
			accuracy: 25.5,
		});
		expect(options.permissions).toEqual(["notifications", "geolocation"]);
	});

	it("puts the defaults in a persistent context's launch", async () => {
		const type = new FakeBrowserType();
		await NewBrowser(type as any, {
			...base(),
			persistent_context: true,
			user_data_dir: "/profile",
		});
		expect(type.launches).toEqual([]);
		const [[userDataDir, options]] = type.persistent;
		expect(userDataDir).toBe("/profile");
		expect(options.viewport).toBeNull();
		expect(options.colorScheme).toBeNull();
		expect(options).not.toHaveProperty("user_data_dir");
	});

	it("refuses user_data_dir without persistent_context, as Playwright-Python does", async () => {
		const type = new FakeBrowserType();
		await expect(
			NewBrowser(type as any, { ...base(), user_data_dir: "/profile" }),
		).rejects.toThrow(/unexpected keyword argument 'user_data_dir'/);
	});

	it("warns for a context option that changes the identity", async () => {
		const type = new FakeBrowserType();
		const browser = await NewBrowser(type as any, base());
		const { warnings: caught } = await warnings.recordWarnings(() =>
			browser.newContext({ userAgent: "x" }),
		);
		expect(caught.map((w) => w.category)).toEqual(["LeakWarning"]);
		expect(caught[0].message).toContain("userAgent= cannot be set per context");
	});

	it("warns that addons on a non-persistent launch will not run", async () => {
		const type = new FakeBrowserType();
		const addon = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-addon-"));
		fs.writeFileSync(path.join(addon, "manifest.json"), "{}");
		const { warnings: caught } = await warnings.recordWarnings(() =>
			NewBrowser(type as any, { ...base(), addons: [addon] }),
		);
		expect(
			caught.some(
				(w) =>
					w.category === "RuntimeWarning" &&
					w.message.includes("persistent_context=True"),
			),
		).toBe(true);
		expect(
			type.launches[0].args.some((arg: string) =>
				arg.startsWith(`--load-extension=${addon}`),
			),
		).toBe(true);
	});

	it("starts a display for headless='virtual', launches headed, and tears it down", async () => {
		const startedAt: [number, number][] = [];
		const get = vi
			.spyOn(VirtualDisplay.prototype, "get")
			.mockImplementation(async function (
				this: InstanceType<typeof VirtualDisplay>,
			) {
				startedAt.push(this.size);
				return ":88";
			});
		const kill = vi
			.spyOn(VirtualDisplay.prototype, "kill")
			.mockImplementation(() => undefined);
		const type = new FakeBrowserType();
		const browser = await NewBrowser(type as any, {
			...base(),
			headless: "virtual",
			env: { HOME, WAYLAND_DISPLAY: "wayland-0" },
		});
		expect(get).toHaveBeenCalledOnce();
		const [launched] = type.launches;
		expect(launched.headless).toBe(false);
		expect(launched.env.DISPLAY).toBe(":88");
		expect(launched.env).not.toHaveProperty("WAYLAND_DISPLAY");
		expect(launched.args[0]).not.toBe("--ozone-platform=x11");
		expect(launched.args).toContain("--ozone-platform=x11");
		// The window renders at the identity's ratio, so the display is the
		// claimed screen in device pixels.
		const config = configOf(launched);
		const dpr = config["window.devicePixelRatio"];
		expect(startedAt).toEqual([
			[
				Math.ceil(config["screen.width"] * dpr),
				Math.ceil(config["screen.height"] * dpr),
			],
		]);
		await browser.close();
		expect(kill).toHaveBeenCalled();
	});

	it("kills the display when the launch fails", async () => {
		vi.spyOn(VirtualDisplay.prototype, "get").mockResolvedValue(":88");
		const kill = vi
			.spyOn(VirtualDisplay.prototype, "kill")
			.mockImplementation(() => undefined);
		const type = new FakeBrowserType();
		type.fail = new Error("no browser");
		await expect(
			NewBrowser(type as any, { ...base(), headless: "virtual" }),
		).rejects.toThrow("no browser");
		expect(kill).toHaveBeenCalled();
		kill.mockClear();
		await expect(
			NewBrowser(type as any, { ...base(), headless: "virtual", os: "macos" }),
		).rejects.toThrow(/macos/);
		expect(kill).toHaveBeenCalled();
	});

	it("refuses gpu options without virtual-gpu", async () => {
		await expect(
			NewBrowser(new FakeBrowserType() as any, { ...base(), gpu: "0" }),
		).rejects.toThrow(ValueError);
	});
});

describe("NewContext", () => {
	it.each([
		["userAgent", "x"],
		["screen", { width: 1, height: 1 }],
		["deviceScaleFactor", 2],
		["isMobile", true],
		["hasTouch", true],
	])("refuses %s: an identity of its own", async (option, value) => {
		const browser = new FakeBrowser();
		await expect(
			NewContext(browser as any, { [option]: value }),
		).rejects.toThrow(IncoherentIdentity);
		await expect(
			NewContext(browser as any, { [option]: value }),
		).rejects.toThrow(`${option}= cannot be set per context`);
		expect(browser.calls).toEqual([]);
	});

	it.each([
		"preset",
		"os",
		"ff_version",
		"chrome_version",
		"webrtc_ip",
	])("does not draw a second fingerprint for %s", async (option) => {
		const error = await NewContext(new FakeBrowser() as any, {
			[option]: "x",
		}).catch((e) => e);
		expect(error).toBeInstanceOf(NotImplementedError);
		expect(error.message).toBe(
			`NewContext(${option}=) is not implemented in Camouwolf: the browser reads one identity ` +
				"per process (CAMOU_CONFIG), so a context cannot have its own fingerprint. Launch " +
				"another browser for another identity.",
		);
	});

	it("places a context", async () => {
		const browser = new FakeBrowser();
		await NewContext(browser as any, {
			proxy: { server: "http://proxy.example:8080" },
			geolocation: { latitude: 1.5, longitude: 2.5 },
			timezoneId: "Europe/Paris",
			locale: "fr-FR",
		});
		expect(browser.calls).toEqual([
			[
				"newContext",
				{
					proxy: { server: "http://proxy.example:8080" },
					geolocation: { latitude: 1.5, longitude: 2.5 },
					permissions: ["geolocation"],
					timezoneId: "Europe/Paris",
					locale: "fr-FR",
				},
			],
		]);
	});
});

describe("NewContext behind a proxy", () => {
	const proxy = {
		server: "http://203.0.113.5:1",
		username: "u",
		password: "p",
	};
	let lookedUp: Array<string | undefined>;
	beforeEach(() => {
		lookedUp = [];
		utils.utilsDeps.geoipAllowed = () => undefined;
		utils.utilsDeps.publicIp = (async (p?: string) => {
			lookedUp.push(p);
			return "198.51.100.7";
		}) as any;
		utils.utilsDeps.getGeolocation = (async () => ({
			longitude: 139.7,
			latitude: 35.7,
			timezone: "Asia/Tokyo",
		})) as any;
	});

	it("is placed where the proxy exits", async () => {
		// Otherwise the context keeps the launch's timezone and position beside
		// the proxy's IP.
		const browser = new FakeBrowser();
		await NewContext(browser as any, { proxy });
		await NewContext(browser as any, { proxy, timezoneId: "Asia/Osaka" });
		expect(lookedUp).toEqual(Array(2).fill("http://u:p@203.0.113.5:1"));
		const placed = (timezoneId: string) => [
			"newContext",
			{
				proxy,
				geolocation: { latitude: 35.7, longitude: 139.7 },
				permissions: ["geolocation"],
				timezoneId,
			},
		];
		expect(browser.calls).toEqual([placed("Asia/Tokyo"), placed("Asia/Osaka")]);
	});

	it("throws when the exit lookup fails", async () => {
		utils.utilsDeps.publicIp = (async () => {
			throw new InvalidIP("Failed to get IP address");
		}) as any;
		const browser = new FakeBrowser();
		await expect(NewContext(browser as any, { proxy })).rejects.toThrow(
			InvalidIP,
		);
		expect(browser.calls).toEqual([]);
	});
});

describe.skipIf(!MODEL.ok)("launchOptions", () => {
	it("keeps WebRTC on the proxied route by default with a proxy", async () => {
		// Otherwise WebRTC's UDP goes around the proxy, and STUN reports the
		// host's own public IP.
		const policy = "--force-webrtc-ip-handling-policy=disable_non_proxied_udp";
		const proxy = { server: "http://127.0.0.1:8080" };
		expect((await utils.launchOptions({ ...base(), proxy })).args).toContain(
			policy,
		);
		expect((await utils.launchOptions(base())).args).not.toContain(policy);
		const { result, warnings: caught } = await warnings.recordWarnings(() =>
			utils.launchOptions({ ...base(), proxy, block_webrtc: false }),
		);
		expect(result?.args).not.toContain(policy);
		expect(caught.map((w) => w.message).join()).toContain("block_webrtc=False");
	});

	it("does not write to the caller's objects", async () => {
		const config = { timezone: "Europe/Berlin" };
		const args = ["--foo"];
		const env = { HOME: "/tmp" };
		await utils.launchOptions({
			...base(),
			config,
			args,
			env,
			i_know_what_im_doing: true,
		});
		expect([config, args, env]).toEqual([
			{ timezone: "Europe/Berlin" },
			["--foo"],
			{ HOME: "/tmp" },
		]);
	});

	it("takes the binary from CAMOUWOLF_EXECUTABLE_PATH", async () => {
		utils.utilsDeps.environ = () => ({
			CAMOUWOLF_EXECUTABLE_PATH: ` ${BUNDLE_EXE} `,
		});
		const { executable_path: _path, ...options } = base();
		const launch = await utils.launchOptions(options);
		expect(launch.executablePath).toBe(BUNDLE_EXE);
		await expect(
			utils.launchOptions({ ...options, browser: "official/alpha.1" }),
		).rejects.toThrow(/CAMOUWOLF_EXECUTABLE_PATH names a binary too/);
	});

	it("uses the installed browser when none is named, installing it first", async () => {
		const order: string[] = [];
		utils.utilsDeps.ensureCamouwolfInstalled = (async () => {
			order.push("install");
		}) as any;
		utils.utilsDeps.launchPath = (() => {
			order.push("path");
			return BUNDLE_EXE;
		}) as any;
		const { executable_path: _path, ...options } = base();
		const launch = await utils.launchOptions(options);
		expect(order).toEqual(["install", "path"]);
		expect(launch.executablePath).toBe(BUNDLE_EXE);
	});

	it("keeps a fixed window when a zoomed ratio is repaired", async () => {
		// The repair rescales the record's CSS sizes to the display's scale; the
		// caller's window is not the record's and stays as asked.
		const drawn = await utils.drawFingerprint("windows", null, null, null);
		const fingerprint = {
			...drawn,
			window: { ...drawn.window, devicePixelRatio: 1.1 },
		};
		const launch = await quietly(() =>
			utils.launchOptions({
				...base(),
				os: undefined,
				fingerprint,
				window: [1200, 800],
			}),
		);
		expect(configOf(launch)["window.devicePixelRatio"]).toBe(1);
		expect(launch.args).toContain("--window-size=1200,800");
	});

	it("always sends the voices, so the host's never show", async () => {
		// Patch 0012 shows the host's voices when the key is absent: a record
		// without voices, and a caller's user agent (no draw), must still send it.
		const fingerprint = {
			...(await utils.drawFingerprint("windows", null, null, null)),
			voices: [],
		};
		const drawn = await quietly(() =>
			utils.launchOptions({ ...base(), os: undefined, fingerprint }),
		);
		expect(configOf(drawn).voices).toEqual([]);
		const ua =
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36";
		const caller = await quietly(() =>
			utils.launchOptions({
				...base(),
				os: undefined,
				config: { "navigator.userAgent": ua },
			}),
		);
		expect(configOf(caller).voices).toEqual([]);
	});

	it("uses a passed fingerprint, and refuses one from another browser", async () => {
		// The launcher's own draw: a raw fpgen record may have a software GPU,
		// which a passed fingerprint is refused for.
		const fingerprint = await utils.drawFingerprint(
			"windows",
			null,
			null,
			null,
		);
		const launch = await quietly(() =>
			utils.launchOptions({ ...base(), os: undefined, fingerprint }),
		);
		const config = configOf(launch);
		expect(config["screen.width"]).toBeGreaterThan(0);
		expect(config["webGl:renderer"]).toBe(fingerprint.gpu.renderer);
		expect(utils.launchOs(launch)).toBe("windows");

		const firefox = {
			navigator: {
				userAgent:
					"Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
			},
		};
		const error = await utils
			.launchOptions({ ...base(), fingerprint: firefox })
			.catch((e) => e);
		expect(error).toBeInstanceOf(NonChromeFingerprint);
		expect(error.message).toContain("Firefox/140.0'");
		await expect(
			utils.launchOptions({
				...base(),
				fingerprint,
				webgl_config: ["a", "b"],
			}),
		).rejects.toThrow(/cannot be combined with `fingerprint`/);
	});

	it("reports the host's core count unless pinned", async () => {
		const cores = async (pin: boolean) =>
			configOf(await utils.launchOptions({ ...base(), pin_cpu_cores: pin }))[
				"navigator.hardwareConcurrency"
			];
		expect(await cores(false)).toBe(16);
		utils.utilsDeps.hostCpu = () => ({ cpuCount: 6, canPinHost: true });
		expect(await cores(false)).toBe(6);
		expect(await cores(true)).toBeLessThanOrEqual(6);
	});

	it("writes chromium_prefs into the profile", async () => {
		const profile = fs.mkdtempSync(path.join(os.tmpdir(), "camouwolf-prefs-"));
		fs.mkdirSync(path.join(profile, "Default"));
		fs.writeFileSync(
			path.join(profile, "Default", "Preferences"),
			JSON.stringify({ profile: { name: "kept" }, other: 1 }),
		);
		const launch = await utils.launchOptions({
			...base(),
			user_data_dir: profile,
			chromium_prefs: {
				credentials_enable_service: false,
				"profile.default_content_setting_values.notifications": 2,
			},
		});
		expect(launch.user_data_dir).toBe(profile);
		expect(
			JSON.parse(
				fs.readFileSync(path.join(profile, "Default", "Preferences"), "utf-8"),
			),
		).toEqual({
			profile: {
				name: "kept",
				default_content_setting_values: { notifications: 2 },
			},
			other: 1,
			credentials_enable_service: false,
		});
	});
});

describe.skipIf(!MODEL.ok)("geoip without a proxy", () => {
	const BERLIN = { timezone: "Europe/Berlin", "locale:region": "DE" };
	const AMSTERDAM = { timezone: "Europe/Amsterdam", "locale:region": "NL" };
	let places: Record<string, Record<string, string>>;
	let ipv6: string | null;
	beforeEach(() => {
		places = { "203.0.113.5": BERLIN };
		ipv6 = null;
		utils.utilsDeps.geoipAllowed = () => undefined;
		utils.utilsDeps.publicIpv6 = async () => ipv6;
		utils.utilsDeps.getGeolocation = (async (ip: string) => {
			if (!(ip in places)) throw new UnknownIPLocation(ip);
			return {
				latitude: 52.5,
				longitude: 13.4,
				asConfig: () => ({ ...places[ip] }),
			};
		}) as any;
	});
	const launch = async (extra: Record<string, unknown> = {}) =>
		configOf(
			await utils.launchOptions({ ...base(), geoip: "203.0.113.5", ...extra }),
		);

	it("is an IPv4-only network on a host without IPv6", async () => {
		expect((await launch())["network:ipv4Only"]).toBe(true);
	});

	it("keeps IPv6 that geolocates with the identity", async () => {
		ipv6 = "2001:db8::5";
		places[ipv6] = BERLIN;
		expect(await launch()).not.toHaveProperty("network:ipv4Only");
	});

	it("turns off IPv6 that geolocates elsewhere, and warns", async () => {
		// An IPv4-only VPN: sites with an IPv6 address would see the host's own.
		ipv6 = "2001:db8::5";
		places[ipv6] = AMSTERDAM;
		const warn = vi.spyOn(warnings.LeakWarning, "warn");
		expect((await launch())["network:ipv4Only"]).toBe(true);
		expect(warn).toHaveBeenCalledWith("ipv6_elsewhere", false);
	});

	it("leaves IPv6 alone behind a proxy", async () => {
		utils.utilsDeps.publicIpv6 = async () => {
			throw new Error("looked up the host IPv6 behind a proxy");
		};
		vi.spyOn(warnings.LeakWarning, "warn").mockImplementation(() => {});
		const config = await launch({ proxy: { server: "http://203.0.113.9:1" } });
		expect(config).not.toHaveProperty("network:ipv4Only");
	});
});
