/**
 * Device and keyboard surfaces, checked against the measured captures.
 *
 * Both of these were patched surfaces the launcher never drove: `browser/` read
 * `mediaDevices:enumerated` (0013) and `keyboard.layoutMap` (0014), and a
 * `--os windows` launch wrote neither, so every Windows persona answered with
 * the Linux host's devices and key layout. `check-config-keys.py` structurally
 * cannot catch this — it finds keys written but never read, and this is the
 * mirror case. See docs/WINDOWS-DIVERGENCE.md §2.5.
 *
 * These assert against `tests/compare/captures/`, the real Chrome 151 recordings
 * from the Windows 11 box and this Linux box, rather than against constants
 * copied out of them. A test that restates the launcher's own table would pass
 * whatever the table said.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { launchOptions } from "../../src/utils.js";

const REPO = path.resolve(
	path.dirname(fileURLToPath(import.meta.url)),
	"../..",
);

function capture(name: string): Record<string, never> {
	return JSON.parse(
		fs.readFileSync(
			path.join(REPO, "tests/compare/captures", `${name}.json`),
			"utf8",
		),
	);
}

function configFrom(built: Record<string, unknown>): Record<string, unknown> {
	const env = built.env as Record<string, string>;
	const json = Object.keys(env)
		.filter((k) => k.startsWith("CAMOU_CONFIG_"))
		.sort((a, b) => Number(a.split("_").pop()) - Number(b.split("_").pop()))
		.map((k) => env[k])
		.join("");
	return JSON.parse(json);
}

async function configFor(os: "windows" | "linux") {
	return configFrom(
		await launchOptions({
			headless: true,
			stealth: true,
			seed: 1234,
			executable_path: "/nonexistent/camouwolf",
			os,
		}),
	);
}

const LEGS = [
	["windows", "windows-real"],
	["linux", "linux-stock"],
] as const;

describe("mediaDevices:enumerated", () => {
	for (const [os, legName] of LEGS) {
		it(`matches the ${legName} device kinds, in order`, async () => {
			const leg = capture(legName) as unknown as {
				media: { devices: { kind: string }[] };
			};
			const expected = leg.media.devices.map((d) => d.kind);
			const packed = (await configFor(os))[
				"mediaDevices:enumerated"
			] as string[];
			expect(packed.map((entry) => entry.split("|")[0])).toEqual(expected);
		});

		it(`leaves id, label and groupId empty for ${os}`, async () => {
			// Measured on both legs: idLen 0, groupLen 0, hasLabel false. A
			// synthetic deviceId before a permission grant is a tell rather than a
			// defence — no real Chrome hands one out.
			const packed = (await configFor(os))[
				"mediaDevices:enumerated"
			] as string[];
			for (const entry of packed) {
				const [kind, deviceId, label, groupId] = entry.split("|");
				expect(kind).not.toBe("");
				expect([deviceId, label, groupId]).toEqual(["", "", ""]);
			}
		});
	}

	it("gives Windows a videoinput that Linux does not have", async () => {
		// The count is the signal, and this is the specific measured divergence:
		// 3 devices on Windows, 2 on Linux.
		const kinds = async (os: "windows" | "linux") =>
			((await configFor(os))["mediaDevices:enumerated"] as string[]).map(
				(e) => e.split("|")[0],
			);
		expect(await kinds("windows")).toContain("videoinput");
		expect(await kinds("linux")).not.toContain("videoinput");
	});
});

describe("keyboard.layoutMap", () => {
	for (const [os, legName] of LEGS) {
		it(`agrees with every key ${legName} sampled`, async () => {
			const leg = capture(legName) as unknown as {
				keyboard: Record<string, string | number | null>;
			};
			const packed = (await configFor(os))["keyboard.layoutMap"] as string[];
			const emitted = new Map(
				packed.map((entry) => {
					const split = entry.indexOf("=");
					return [entry.slice(0, split), entry.slice(split + 1)];
				}),
			);

			for (const [code, value] of Object.entries(leg.keyboard)) {
				if (code === "__size") continue;
				if (value === null) {
					// IntlRo / IntlYen are JIS keys, absent from a US layout on both
					// operating systems. Emitting them would change the map's size.
					expect(emitted.has(code), `${code} should be absent`).toBe(false);
					continue;
				}
				expect(emitted.get(code), `${code} mismatch`).toBe(value);
			}
		});

		it(`reports the ${legName} map size`, async () => {
			const leg = capture(legName) as unknown as {
				keyboard: { __size: number };
			};
			const packed = (await configFor(os))["keyboard.layoutMap"] as string[];
			expect(packed.length).toBe(leg.keyboard.__size);
		});
	}

	it("is the one key that differs between the two operating systems", async () => {
		// IntlBackslash: `\` on Windows, `<` on Linux. Everything else agrees, so
		// the map is otherwise shared and this single entry is the measured leak.
		const entries = async (os: "windows" | "linux") =>
			new Set((await configFor(os))["keyboard.layoutMap"] as string[]);
		const win = await entries("windows");
		const lin = await entries("linux");
		const onlyWin = [...win].filter((e) => !lin.has(e));
		const onlyLin = [...lin].filter((e) => !win.has(e));
		expect(onlyWin).toEqual(["IntlBackslash=\\"]);
		expect(onlyLin).toEqual(["IntlBackslash=<"]);
	});

	it("packs entries the way 0014 parses them", async () => {
		// 0014 splits on the FIRST '=', so `Equal==` must mean Equal -> "=".
		const packed = (await configFor("windows"))[
			"keyboard.layoutMap"
		] as string[];
		const equal = packed.find((e) => e.startsWith("Equal="));
		expect(equal).toBe("Equal==");
		const split = (equal as string).indexOf("=");
		expect((equal as string).slice(0, split)).toBe("Equal");
		expect((equal as string).slice(split + 1)).toBe("=");
	});
});
