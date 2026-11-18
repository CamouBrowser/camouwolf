#!/usr/bin/env node
/**
 * Build `src/data-files/fingerprint-presets.json` — the real-fingerprint pool.
 *
 * ## Where the data comes from
 *
 * The device half is drawn from **Apify's `fingerprint-generator`** (the
 * `fingerprint-suite` project), whose Bayesian network is fitted to a corpus of
 * fingerprints collected from real browsers. Sampling that network gives
 * combinations of UA / screen geometry / core count / device memory / language
 * that co-occurred on real machines, rather than a cartesian product of
 * plausible-looking values.
 *
 * That is the same source the launcher uses for its *synthetic* default draw,
 * so why materialise a pool at all? Because a preset is **frozen and audited**:
 * every entry in the emitted file has been through the coherence checks below
 * and through `tests/compare/preset-audit.mjs`, so a preset launch cannot draw
 * an identity that contradicts itself. The live draw can, and the launcher has
 * to repair it at runtime. The trade is the usual one — a finite, verified pool
 * against an unbounded, unverified one.
 *
 * ## What is NOT taken from the network
 *
 * - **GPU adapter.** The network's `videoCard` pool is heavy with software
 *   adapters ("Microsoft Basic Render Driver"), which on a real desktop means a
 *   VM with no GPU. Adapters come from `webgl_adapters.json` instead, drawn as
 *   whole records so the renderer string and the numeric limits agree.
 * - **Client hints.** Derived from the identity by `clienthints.ts` so the UA
 *   and the hints cannot disagree. The network has no UA-CH data.
 * - **Fonts and voices.** Left to the manifest model at launch time, because
 *   that model is filtered to what the build can actually render/speak. A preset
 *   claiming a font this binary does not have is a lie one measureText exposes.
 *
 * Usage:  node scripts/gen-fingerprint-presets.mjs [--count 250] [--out PATH]
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FingerprintGenerator } from "fingerprint-generator";

const REPO = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// ---------------------------------------------------------------- args
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
	const i = argv.indexOf(name);
	return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};
const PER_OS = Number(arg("--count", "250"));
const OUT = arg("--out", path.join(REPO, "src/data-files/fingerprint-presets.json"));

// ------------------------------------------------------------- pinned version
function pinned() {
	const text = fs.readFileSync(path.join(REPO, "browser/upstream.sh"), "utf8");
	const grab = (key) => text.match(new RegExp(`^${key}=(.+)$`, "m"))?.[1]?.trim();
	return { version: grab("version"), milestone: Number(grab("milestone")) };
}
const { version: FULL_VERSION, milestone: MILESTONE } = pinned();

const ADAPTERS = JSON.parse(
	fs.readFileSync(path.join(REPO, "src/data-files/webgl_adapters.json"), "utf8"),
);

// Sec-CH-UA-Platform-Version ladders. Kept in sync with src/clienthints.ts —
// clienthints.test.ts fails if these two ever disagree, because a generator
// drawing a rung assertCoherent() rejects produces a preset pool the launcher
// refuses to launch.
const PLATFORM_VERSIONS = {
	windows: ["10.0.0", "13.0.0", "14.0.0", "15.0.0", "19.0.0"],
	macos: ["14.5.0", "14.6.1", "15.1.0", "15.2.0", "15.3.1"],
	linux: ["6.8.0", "6.11.0", "6.12.0"],
};

const UA_TOKEN = {
	windows: "Windows NT 10.0; Win64; x64",
	macos: "Macintosh; Intel Mac OS X 10_15_7",
	linux: "X11; Linux x86_64",
};

const NAV_PLATFORM = {
	windows: "Win32",
	macos: "MacIntel",
	linux: "Linux x86_64",
};

const CH_PLATFORM = { windows: "Windows", macos: "macOS", linux: "Linux" };

/** navigator.deviceMemory is quantized by spec; an off-ladder value is a tell. */
const DEVICE_MEMORY_LADDER = [0.25, 0.5, 1, 2, 4, 8];

function snapDeviceMemory(value) {
	if (!value) return 8;
	let best = DEVICE_MEMORY_LADDER[0];
	for (const step of DEVICE_MEMORY_LADDER) {
		if (Math.abs(step - value) < Math.abs(best - value)) best = step;
	}
	return best;
}

/**
 * Visible OS chrome height. A desktop where availHeight === height on BOTH axes
 * is what stock headless reports, and CreepJS raises `noTaskbar` on it.
 */
const TASKBAR_INSET = { windows: 40, macos: 25, linux: 27 };

function coherent(preset, osName) {
	const problems = [];
	const s = preset.screen;
	if (!s.width || !s.height) problems.push("no screen geometry");
	if (s.availWidth > s.width || s.availHeight > s.height) {
		problems.push("available rect larger than the screen");
	}
	if (s.availWidth === s.width && s.availHeight === s.height) {
		problems.push("no taskbar inset on either axis");
	}
	// Real desktops are not 640x480. The network's tail includes tiny mobile-ish
	// geometries that make no sense under a desktop UA.
	if (s.width < 1024 || s.height < 600) problems.push("implausibly small screen");
	// navigator.hardwareConcurrency: a consumer-desktop range.
	//
	// The network's desktop slice has a long tail of server core counts — 144,
	// 512, 640 all appear — because the corpus includes machines that are not
	// consumer desktops. For a residential identity those are the WORST possible
	// value: a browser claiming 144 cores is announcing a datacenter, which is
	// the single thing the whole identity is trying not to look like. 32 covers
	// essentially every real consumer machine; above it is a workstation or a
	// server. Odd counts are kept — Intel hybrid CPUs with disabled cores and
	// VMs both produce them, and the pool's entropy comes from that tail.
	const hc = preset.navigator.hardwareConcurrency;
	if (!Number.isInteger(hc)) problems.push("non-integer hardwareConcurrency");
	if (hc < 2) problems.push("hardwareConcurrency < 2");
	if (hc > 32) problems.push(`hardwareConcurrency ${hc} is a server core count`);
	if (!preset.navigator.userAgent.includes(UA_TOKEN[osName])) {
		problems.push("UA platform token does not match the OS");
	}
	if (preset.navigator.platform !== NAV_PLATFORM[osName]) {
		problems.push("navigator.platform does not match the OS");
	}
	if (preset.userAgentData.platform !== CH_PLATFORM[osName]) {
		problems.push("Sec-CH-UA-Platform does not match the OS");
	}
	if (!PLATFORM_VERSIONS[osName].includes(preset.userAgentData.platformVersion)) {
		problems.push("platformVersion off the ladder");
	}
	// macOS Chrome is universal-binary; a Mac claiming x86 with an Apple Silicon
	// GPU (or arm with an Intel one) is a contradiction.
	if (osName === "macos") {
		const apple = /Apple M\d/.test(preset.webgl.unmaskedRenderer);
		if (apple && preset.userAgentData.architecture !== "arm") {
			problems.push("Apple Silicon GPU with x86 architecture hint");
		}
		if (!apple && preset.userAgentData.architecture !== "x86") {
			problems.push("Intel Mac GPU with arm architecture hint");
		}
	}
	return problems;
}

function buildOne(gen, osName, adapters) {
	const { fingerprint } = gen.getFingerprint({
		browsers: ["chrome"],
		operatingSystems: [osName],
		devices: ["desktop"],
	});

	const nav = fingerprint.navigator;
	const screen = fingerprint.screen;

	// Force the UA onto the pinned milestone and the frozen platform token. The
	// network's UAs lag by several versions and occasionally carry a live OS
	// version, which Chrome's UA reduction froze years ago.
	const userAgent =
		`Mozilla/5.0 (${UA_TOKEN[osName]}) AppleWebKit/537.36 ` +
		`(KHTML, like Gecko) Chrome/${MILESTONE}.0.0.0 Safari/537.36`;

	const width = screen.width;
	const height = screen.height;
	let availWidth = screen.availWidth || width;
	let availHeight = screen.availHeight || height;
	if (availWidth >= width && availHeight >= height) {
		availHeight = height - TASKBAR_INSET[osName];
	}
	availWidth = Math.min(availWidth, width);
	availHeight = Math.min(availHeight, height);

	const adapter = adapters[Math.floor(Math.random() * adapters.length)];
	const appleSilicon = /Apple M\d/.test(adapter.renderer);
	const architecture =
		osName === "macos" ? (appleSilicon ? "arm" : "x86") : "x86";

	return {
		navigator: {
			userAgent,
			platform: NAV_PLATFORM[osName],
			hardwareConcurrency: nav.hardwareConcurrency,
			deviceMemory: snapDeviceMemory(nav.deviceMemory),
			// Desktop Chrome reports 0 unless the machine has a touchscreen; the
			// network's desktop slice occasionally carries a mobile value.
			maxTouchPoints: nav.maxTouchPoints > 0 && nav.maxTouchPoints <= 10
				? nav.maxTouchPoints
				: 0,
			languages: Array.isArray(nav.languages) && nav.languages.length
				? nav.languages
				: ["en-US", "en"],
		},
		userAgentData: {
			platform: CH_PLATFORM[osName],
			platformVersion:
				PLATFORM_VERSIONS[osName][
					Math.floor(Math.random() * PLATFORM_VERSIONS[osName].length)
				],
			architecture,
			bitness: "64",
			model: "",
			mobile: false,
			wow64: false,
			uaFullVersion: FULL_VERSION,
		},
		screen: {
			width,
			height,
			availWidth,
			availHeight,
			colorDepth: screen.colorDepth || 24,
			devicePixelRatio: screen.devicePixelRatio || 1,
		},
		webgl: {
			unmaskedVendor: adapter.vendor,
			unmaskedRenderer: adapter.renderer,
			parameters: adapter.parameters,
		},
		webgpu: adapter.webgpu,
	};
}

/** Identity key for de-duplication: two presets differing only in noise are one. */
function key(p) {
	return [
		p.navigator.platform,
		p.navigator.hardwareConcurrency,
		p.navigator.deviceMemory,
		p.screen.width,
		p.screen.height,
		p.screen.availHeight,
		p.screen.devicePixelRatio,
		p.webgl.unmaskedRenderer,
		p.userAgentData.platformVersion,
	].join("|");
}

// ------------------------------------------------------------------- generate
const gen = new FingerprintGenerator();
const presets = {};
const stats = {};

for (const [osName, adapterKey] of [
	["windows", "windows"],
	["macos", "macos"],
	["linux", "linux"],
]) {
	const adapters = ADAPTERS[adapterKey];
	const seen = new Set();
	const out = [];
	let rejected = 0;
	let attempts = 0;
	// Cap attempts so an over-strict check cannot spin forever; report the
	// shortfall rather than silently emitting a thin pool.
	const maxAttempts = PER_OS * 60;

	while (out.length < PER_OS && attempts < maxAttempts) {
		attempts++;
		let preset;
		try {
			preset = buildOne(gen, osName, adapters);
		} catch {
			continue;
		}
		const problems = coherent(preset, osName);
		if (problems.length) {
			rejected++;
			continue;
		}
		const k = key(preset);
		if (seen.has(k)) continue;
		seen.add(k);
		out.push(preset);
	}

	presets[osName] = out;
	stats[osName] = { emitted: out.length, rejected, attempts };
	const short = out.length < PER_OS ? `  (SHORT of ${PER_OS})` : "";
	console.log(
		`  ${osName.padEnd(8)} ${String(out.length).padStart(4)} unique  ` +
			`${String(rejected).padStart(5)} rejected  ` +
			`${String(attempts).padStart(6)} draws${short}`,
	);
}

const bundle = {
	version: 1,
	chromium: FULL_VERSION,
	milestone: MILESTONE,
	source: "apify/fingerprint-suite (fingerprint-generator), chrome + desktop",
	notes:
		"Device half only. Client hints are derived at launch by clienthints.ts; " +
		"fonts and voices come from the manifest model, filtered to what the " +
		"build can render.",
	presets,
};

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, `${JSON.stringify(bundle, null, "\t")}\n`);

const total = Object.values(presets).reduce((n, a) => n + a.length, 0);
console.log(`\n-> ${path.relative(REPO, OUT)}  (${total} presets)`);
if (Object.values(stats).some((s) => s.emitted < PER_OS)) {
	console.error(
		"\n!! at least one OS came up short — the network's desktop slice may not " +
			"hold that many distinct coherent machines. Lower --count or widen the " +
			"adapter table.",
	);
	process.exit(1);
}
