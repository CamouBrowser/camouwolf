/**
 * `geoip` resolves a latitude/longitude alongside the timezone and locale
 * precisely so the three cannot come from different places. Those coordinates
 * used to be written into CAMOU_CONFIG and never read, which left a browser
 * whose timezone said Berlin and whose getCurrentPosition() reported the host —
 * or failed outright on a machine with no location provider.
 *
 * These tests pin the path that carries them to the browser.
 */
import { describe, expect, it, vi } from "vitest";

import {
	applyGeolocationDefaults,
	configFromLaunchEnv,
	geolocationFromConfig,
	launchOptions,
} from "../../src/utils.js";

describe("configFromLaunchEnv", () => {
	it("reassembles a chunked config in index order", () => {
		// The chunks are numbered, not lexicographic: 10 sorts before 2 as a
		// string, and reassembling in that order yields invalid JSON.
		const whole = JSON.stringify({ timezone: "Europe/Berlin" });
		const env: Record<string, string> = {};
		whole.split("").forEach((ch, i) => {
			env[`CAMOU_CONFIG_${i + 1}`] = ch;
		});
		expect(configFromLaunchEnv(env)).toEqual({ timezone: "Europe/Berlin" });
	});

	it("ignores unrelated environment variables", () => {
		expect(
			configFromLaunchEnv({
				PATH: "/usr/bin",
				CAMOU_CONFIG_1: '{"timezone":"UTC"}',
				CAMOUWOLF_UCRT_MATH: "1",
			}),
		).toEqual({ timezone: "UTC" });
	});

	it("returns empty for a missing or unparseable config", () => {
		expect(configFromLaunchEnv(undefined)).toEqual({});
		expect(configFromLaunchEnv({})).toEqual({});
		expect(configFromLaunchEnv({ CAMOU_CONFIG_1: "{not json" })).toEqual({});
	});
});

describe("geolocationFromConfig", () => {
	it("reads latitude and longitude", () => {
		expect(
			geolocationFromConfig({
				"geolocation:latitude": 52.52,
				"geolocation:longitude": 13.405,
			}),
		).toEqual({ latitude: 52.52, longitude: 13.405 });
	});

	it("carries accuracy when present", () => {
		expect(
			geolocationFromConfig({
				"geolocation:latitude": 52.52,
				"geolocation:longitude": 13.405,
				"geolocation:accuracy": 50,
			}),
		).toEqual({ latitude: 52.52, longitude: 13.405, accuracy: 50 });
	});

	it("returns null unless BOTH coordinates are numbers", () => {
		// Half a position is worse than none: Playwright would reject it, and a
		// launch should not fail over a partially-resolved geoip lookup.
		expect(geolocationFromConfig({})).toBeNull();
		expect(geolocationFromConfig({ "geolocation:latitude": 52.52 })).toBeNull();
		expect(
			geolocationFromConfig({
				"geolocation:latitude": 52.52,
				"geolocation:longitude": "13.405",
			}),
		).toBeNull();
	});

	it("accepts zero, which is a real coordinate", () => {
		expect(
			geolocationFromConfig({
				"geolocation:latitude": 0,
				"geolocation:longitude": 0,
			}),
		).toEqual({ latitude: 0, longitude: 0 });
	});
});

describe("applyGeolocationDefaults", () => {
	const geo = { latitude: 52.52, longitude: 13.405 };

	it("sets it directly on a persistent context", async () => {
		const context = {
			setGeolocation: vi.fn().mockResolvedValue(undefined),
			grantPermissions: vi.fn().mockResolvedValue(undefined),
		};
		applyGeolocationDefaults(context, geo);
		await vi.waitFor(() => expect(context.setGeolocation).toHaveBeenCalled());
		expect(context.setGeolocation).toHaveBeenCalledWith(geo);
		expect(context.grantPermissions).toHaveBeenCalledWith(["geolocation"]);
	});

	it("injects it into every context a browser creates", async () => {
		const newContext = vi.fn().mockResolvedValue({});
		const browser = { newContext };
		applyGeolocationDefaults(browser, geo);

		await browser.newContext();
		expect(newContext).toHaveBeenCalledWith(
			expect.objectContaining({
				geolocation: geo,
				permissions: ["geolocation"],
			}),
		);
	});

	it("leaves a caller's own geolocation alone", async () => {
		const newContext = vi.fn().mockResolvedValue({});
		const browser = { newContext };
		applyGeolocationDefaults(browser, geo);

		const mine = { latitude: 1, longitude: 2 };
		await browser.newContext({ geolocation: mine, permissions: ["camera"] });
		expect(newContext).toHaveBeenCalledWith(
			expect.objectContaining({
				geolocation: mine,
				permissions: ["camera"],
			}),
		);
	});

	it("preserves the caller's other context options", async () => {
		const newContext = vi.fn().mockResolvedValue({});
		const browser = { newContext };
		applyGeolocationDefaults(browser, geo);

		await browser.newContext({ locale: "de-DE" });
		expect(newContext).toHaveBeenCalledWith(
			expect.objectContaining({ locale: "de-DE", geolocation: geo }),
		);
	});

	it("is a no-op when no position was resolved", async () => {
		const newContext = vi.fn().mockResolvedValue({});
		const browser = { newContext };
		applyGeolocationDefaults(browser, null);

		await browser.newContext({ locale: "de-DE" });
		expect(newContext).toHaveBeenCalledWith({ locale: "de-DE" });
	});
});

describe("the launch path", () => {
	it("still emits the geolocation keys into the config", async () => {
		// The C++ side does not read these — the coordinates travel through
		// Playwright — but they stay in the config because it is the identity's
		// single source of truth, and NewBrowser reads them back from there
		// rather than recomputing.
		const built = await launchOptions({
			headless: true,
			os: "windows",
			seed: 1234,
			// No bundle is installed in CI; the launcher falls back to the pinned
			// milestone, exactly as launcher-invariants.test.ts does.
			executable_path: "/nonexistent/camouwolf",
			config: {
				"geolocation:latitude": 52.52,
				"geolocation:longitude": 13.405,
			},
		});
		const config = configFromLaunchEnv(built.env as Record<string, unknown>);
		expect(geolocationFromConfig(config)).toEqual({
			latitude: 52.52,
			longitude: 13.405,
		});
	});
});
