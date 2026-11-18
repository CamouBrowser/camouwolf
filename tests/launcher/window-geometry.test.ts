/**
 * The identity's window geometry must reach the real window.
 *
 * Chromium computes window.outerWidth/outerHeight/screenX/screenY from the real
 * window rect, so these cannot be spoofed independently — the reported chrome
 * height (outer - inner) would become a number no window manager produces.
 * Moving the actual window is the only coherent version, and it makes
 * innerWidth/innerHeight follow for free.
 */
import { describe, expect, it } from "vitest";

import { launchOptions } from "../../src/utils.js";

async function build(over: Record<string, unknown> = {}) {
	return launchOptions({
		headless: true,
		stealth: true,
		seed: 4242,
		executable_path: "/nonexistent/camouwolf",
		...over,
	});
}

function argValue(args: string[], flag: string): string | undefined {
	const hit = args.find((a) => a.startsWith(`${flag}=`));
	return hit?.slice(flag.length + 1);
}

describe("window sizing", () => {
	it("sizes the real window from the identity", async () => {
		const built = await build({
			config: { "window.outerWidth": 1600, "window.outerHeight": 900 },
		});
		expect(argValue(built.args as string[], "--window-size")).toBe("1600,900");
	});

	it("positions the real window from the identity", async () => {
		const built = await build({
			config: { "window.screenX": 120, "window.screenY": 64 },
		});
		expect(argValue(built.args as string[], "--window-position")).toBe(
			"120,64",
		);
	});

	it("positions at the origin when the identity says so", async () => {
		// 0,0 is a legitimate position; it must not be dropped as falsy.
		const built = await build({
			config: { "window.screenX": 0, "window.screenY": 0 },
		});
		expect(argValue(built.args as string[], "--window-position")).toBe("0,0");
	});

	it("emits both switches for a generated identity", async () => {
		// The generator always produces a window rect, so a plain launch should
		// carry both rather than leaving the window wherever Chromium put it.
		const args = (await build()).args as string[];
		expect(argValue(args, "--window-size")).toMatch(/^\d+,\d+$/);
		expect(argValue(args, "--window-position")).toMatch(/^\d+,\d+$/);
	});

	it("keeps the window inside the identity's screen", async () => {
		const built = await build();
		const config = JSON.parse(
			Object.keys(built.env as Record<string, string>)
				.filter((k) => k.startsWith("CAMOU_CONFIG_"))
				.sort((a, b) => Number(a.split("_").pop()) - Number(b.split("_").pop()))
				.map((k) => (built.env as Record<string, string>)[k])
				.join(""),
		);
		const [w, h] = (argValue(built.args as string[], "--window-size") ?? "")
			.split(",")
			.map(Number);
		const [x, y] = (argValue(built.args as string[], "--window-position") ?? "")
			.split(",")
			.map(Number);
		// A window that extends past the screen it claims is geometry no display
		// can produce, and it is checkable in one line of JS.
		expect(x + w).toBeLessThanOrEqual(config["screen.width"]);
		expect(y + h).toBeLessThanOrEqual(config["screen.height"]);
	});

	it("still refuses a CALLER's --window-size", async () => {
		// The refusal exists because a caller's switch moves the real window
		// without moving the config. Camouwolf driving it FROM the config is the
		// inverse, so only the caller path stays refused.
		await expect(build({ args: ["--window-size=800,600"] })).rejects.toThrow(
			/--window-size/,
		);
	});
});
