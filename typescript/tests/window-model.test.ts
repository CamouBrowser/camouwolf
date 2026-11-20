import { describe, expect, it } from "vitest";
import { chromeWindowReports } from "../src/fingerprints.js";

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
