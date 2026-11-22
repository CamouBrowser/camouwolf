import { describe, expect, it } from "vitest";
import {
	chromeViewportWidth,
	chromeWindowReports,
	snapWindowToDevicePixels,
} from "../src/fingerprints.js";

// Windows Chrome opened at 1.75 on X11, [position, size] asked -> read back.
const MEASURED_AT_1_75: [[number, number], [number, number]][] = [
	[
		[0, 1100],
		[0, 1100],
	],
	[
		[0, 476],
		[0, 477],
	],
	[
		[0, 1000],
		[0, 1001],
	],
	[
		[0, 480],
		[0, 481],
	],
	[
		[0, 484],
		[0, 485],
	],
	[
		[0, 800],
		[0, 801],
	],
	[
		[8, 800],
		[8, 801],
	],
	[
		[4, 600],
		[4, 600],
	],
	[
		[0, 1096],
		[0, 1096],
	],
	[
		[0, 616],
		[0, 616],
	],
	[
		[4, 476],
		[4, 477],
	],
	[
		[7, 1104],
		[6, 1106],
	],
	[
		[7, 479],
		[6, 481],
	],
];

describe("the window model", () => {
	it.each(MEASURED_AT_1_75)("is what Chrome reported for %j", (given, seen) => {
		expect(chromeWindowReports(given[0], given[1], 1.75)).toEqual(seen);
	});
});

// Windows Chrome laid out on X11, [devicePixelRatio, outerWidth] -> innerWidth.
const MEASURED_VIEWPORTS: [[number, number], number][] = [
	[[1.25, 1200], 1200],
	[[1.25, 1201], 1202],
	[[1.25, 1202], 1202],
	[[1.25, 1205], 1206],
	[[1.25, 1536], 1536],
	[[1.25, 1537], 1538],
	[[1.5, 1202], 1202],
	[[1.75, 1201], 1201],
	[[1.75, 1000], 1000],
	[[2.5, 1203], 1203],
	[[3, 1505], 1505],
];

describe("the viewport model", () => {
	it.each(
		MEASURED_VIEWPORTS,
	)("is what Chrome reported for %j", (given, seen) => {
		expect(chromeViewportWidth(given[1], given[0])).toBe(seen);
	});

	it("never leaves the claimed window narrower than its page", () => {
		// A windows-1 build-tester profile: Chrome reported it 1537 wide with an innerWidth of 1538.
		const config: Record<string, unknown> = {
			"window.devicePixelRatio": 1.25,
			"window.screenX": 2,
			"window.screenY": 0,
			"window.outerWidth": 1537,
			"window.outerHeight": 832,
		};
		snapWindowToDevicePixels(config);
		const width = config["window.outerWidth"] as number;
		expect(chromeViewportWidth(width, 1.25)).toBeLessThanOrEqual(width);
	});
});
