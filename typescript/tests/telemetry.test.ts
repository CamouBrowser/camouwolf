/**
 * Telemetry: every context of a launch is watched, and it never breaks one.
 * Twin of python/tests/test_telemetry.py.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MODEL } from "./fpgen-setup.js";
import { BUNDLE_EXE, HOME, restoreDeps, stubHost } from "./launch-host.js";

const { NewBrowser } = await import("../src/sync_api.js");
const telemetry = await import("camoucore/telemetry.js");

class Events {
	handlers: Record<string, Array<(...args: any[]) => void>> = {};
	on(event: string, handler: (...args: any[]) => void) {
		this.handlers[event] = [...(this.handlers[event] ?? []), handler];
	}
	emit(event: string) {
		for (const handler of this.handlers[event] ?? []) handler();
	}
	pages() {
		return [];
	}
}

class Browser extends Events {
	async newContext() {
		return new Events();
	}
	async newPage() {
		const context = new Events();
		return { context: () => context };
	}
}

const base = () => ({
	headless: true,
	executable_path: BUNDLE_EXE,
	exclude_addons: ["UBO" as const],
	env: { HOME },
	telemetry: true,
});

let watched: any[];
let made: any[];

beforeEach(() => {
	stubHost("lin");
	delete process.env.CAMOUWOLF_TELEMETRY;
	watched = [];
	made = [];
	vi.spyOn(telemetry.Telemetry.prototype, "watch").mockImplementation(function (
		this: any,
		context: any,
	) {
		if (!made.includes(this)) made.push(this);
		watched.push(context);
	});
	vi.spyOn(telemetry.Telemetry.prototype, "flush").mockResolvedValue();
});

afterEach(() => {
	process.env.CAMOUWOLF_TELEMETRY = "0";
	restoreDeps();
	vi.restoreAllMocks();
});

const launch = (browser: any, options: object) =>
	NewBrowser({ launch: async () => browser } as any, {
		...base(),
		...options,
	});

describe.skipIf(!MODEL.ok)("telemetry", () => {
	it("watches every context and page", async () => {
		const browser = new Browser();
		const launched: any = await launch(browser, { os: "windows" });
		const context = await launched.newContext();
		const page = await launched.newPage();
		expect(watched).toEqual([context, page.context()]);
		expect(browser.handlers.disconnected).toHaveLength(1);
		expect(made[0].dimensions).toMatchObject({
			product: "camouwolf",
			pro: false,
			cross_os: true,
			passthrough: false,
			proxy_provider: "none",
		});
	});

	it("says passthrough, and a same-OS launch is not cross-OS", async () => {
		for (const options of [{ os: "linux" }, { passthrough: true }]) {
			await (await launch(new Browser(), options)).newContext();
		}
		expect(
			made.map((t) => [t.dimensions.cross_os, t.dimensions.passthrough]),
		).toEqual([
			[false, false],
			[false, true],
		]);
	});

	it("watches a persistent context and closes with it", async () => {
		const context = new Events() as any;
		context.close = async () => undefined;
		const close = vi.spyOn(telemetry.Telemetry.prototype, "close");
		await NewBrowser({ launchPersistentContext: async () => context } as any, {
			...base(),
			os: "linux",
			persistent_context: true,
		});
		expect(watched).toEqual([context]);
		context.emit("close");
		expect(close).toHaveBeenCalled();
	});

	it("creates nothing when off", async () => {
		await (
			await launch(new Browser(), { os: "linux", telemetry: false })
		).newContext();
		process.env.CAMOUWOLF_TELEMETRY = "0";
		await (await launch(new Browser(), { os: "linux" })).newContext();
		expect(watched).toEqual([]);
	});

	it("never breaks a launch", async () => {
		vi.spyOn(telemetry.Telemetry.prototype, "watch").mockImplementation(() => {
			throw new Error("telemetry broke");
		});
		const browser = new Browser();
		const launched: any = await launch(browser, { os: "linux" });
		expect(await launched.newContext()).toBeInstanceOf(Events);
	});
});
