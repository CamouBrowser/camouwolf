/**
 * The acceptance test of the port: launchOptions() returns what the Python
 * launcher's launch_options() returns, for the cases recorded in
 * tests/golden/launch.json (scripts/golden/launch_golden.py; `pnpm golden`
 * re-records them, and `--check` there fails when they are stale).
 *
 * Compared exactly: the executable, args, the whole environment (so the
 * CAMOU_CONFIG_<n> chunks byte for byte, and with them every config value and
 * its order), headless, ignoreDefaultArgs, proxy, passed-through Playwright
 * options, every warning (category and text, in order), the `debug` lines for
 * dropped switches, and for a refused launch the error's type and message.
 *
 * Where the two cannot be the same, narrowly:
 *  - Key names. Playwright's JS API names the launch options executablePath
 *    and ignoreDefaultArgs where Python's has executable_path and
 *    ignore_default_args; they are renamed before comparing. Nothing else is.
 *  - The random source. Python's fpgen draws from the `random` module; fpgen-js
 *    draws from Math.random(), which cannot be seeded. The test routes
 *    Math.random() to python-random's seeded stream, and the draws are then
 *    the same ones. A production launch is not reproducible from a seed in
 *    either launcher: this only makes the comparison possible.
 *  - debug=True's config dump. Python prints pprint(config); the layout of
 *    util.inspect() differs, so only the "[camouwolf] dropped ..." lines of
 *    the output are compared.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as random from "python-random";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { MODEL } from "./fpgen-setup.js";
import {
	BUNDLE,
	BUNDLE_EXE,
	CACHE,
	FIXTURES,
	type GoldenHost,
	restoreDeps,
	stubHost,
	utils,
	warnings,
} from "./launch-host.js";

interface GoldenCase {
	name: string;
	seed: number;
	options: Record<string, any>;
	result?: Record<string, any>;
	error?: { type: string; message: string };
	warnings: Array<[string, string]>;
	stdout: string[];
}

const GOLDEN: {
	host: GoldenHost;
	env: Record<string, string>;
	cases: GoldenCase[];
} = JSON.parse(
	fs.readFileSync(
		path.join(import.meta.dirname, "golden", "launch.json"),
		"utf-8",
	),
);

/** The Python exception a thrown TS error stands for. */
function pythonType(error: any): string {
	// Python's RuntimeError is a plain Error here.
	return error?.name === "Error" ? "RuntimeError" : String(error?.name);
}

/** `value` with this machine's paths replaced by the goldens' placeholders. */
function portable(value: string): string {
	return value
		.replaceAll(BUNDLE, "<BUNDLE>")
		.replaceAll(FIXTURES, "<FIXTURES>")
		.replaceAll(CACHE, "<CACHE>");
}

const realRandom = Math.random;

beforeAll(() => {
	fs.mkdirSync(BUNDLE, { recursive: true });
	if (!fs.existsSync(BUNDLE_EXE)) {
		fs.writeFileSync(BUNDLE_EXE, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
	}
	Math.random = () => random.random();
});

afterAll(() => {
	Math.random = realRandom;
});

afterEach(() => {
	restoreDeps();
});

describe.skipIf(!MODEL.ok)(
	"launchOptions() equals Python's launch_options()",
	() => {
		it("has goldens to compare", () => {
			expect(GOLDEN.cases.length).toBeGreaterThan(60);
		});

		for (const golden of GOLDEN.cases) {
			it(golden.name, async () => {
				const { host = "lin", hasDisplay = false, ...options } = golden.options;
				stubHost(host, hasDisplay, GOLDEN.host);
				const printed: string[] = [];
				utils.utilsDeps.print = (line: string) => {
					printed.push(line);
				};
				options.env ??= { ...GOLDEN.env };
				if (!("headless" in options)) options.headless = true;
				options.executable_path = BUNDLE_EXE;
				if (options.addons) {
					options.addons = options.addons.map((p: string) =>
						p.replaceAll("<FIXTURES>", FIXTURES),
					);
				}

				random.seed(golden.seed);
				const run = await warnings.recordWarnings(() =>
					utils.launchOptions(options),
				);

				expect(
					run.warnings.map((w) => [w.category, portable(w.message)]),
				).toEqual(golden.warnings);
				expect(
					printed.filter((line) => line.startsWith("[camouwolf]")),
				).toEqual(golden.stdout);
				if (golden.error) {
					expect(run.error, "Python refused this launch").toBeDefined();
					const error = run.error as Error;
					expect({
						type: pythonType(error),
						message: portable(error.message),
					}).toEqual(golden.error);
					return;
				}
				if (run.error) throw run.error;
				const { executablePath, ignoreDefaultArgs, ...rest } =
					run.result as Record<string, any>;
				const result = {
					executable_path: portable(String(executablePath)),
					ignore_default_args: ignoreDefaultArgs,
					...rest,
					args: (rest.args as string[]).map(portable),
				};
				// The config first, so a difference reads as a config key.
				expect(utils.launchConfig({ env: result.env })).toEqual(
					utils.launchConfig({ env: golden.result?.env }),
				);
				expect(utils.camouConfigBlob({ env: result.env })).toBe(
					utils.camouConfigBlob({ env: golden.result?.env }),
				);
				expect(result).toEqual(golden.result);
				expect(Object.keys(result.env)).toEqual(
					Object.keys(golden.result?.env),
				);
			});
		}
	},
);
