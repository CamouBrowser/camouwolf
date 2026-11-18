/**
 * WebGL identities, from the Chrome devices fpgen has recorded.
 *
 * Chrome reports its GPU through ANGLE, and the renderer string names the
 * ANGLE backend: Direct3D 11 on Windows, Metal (or OpenGL 4.1 on older Macs)
 * on macOS, desktop OpenGL or Vulkan on Linux. A renderer whose backend the
 * claimed OS never uses is a contradiction a page reads in one call, so every
 * identity's renderer is checked against its OS (backendMatchesOs).
 *
 * The browser answers getParameter() for the pnames in `webGl:parameters`
 * before its own lookup, for either context version and whether or not the
 * extension a pname belongs to is enabled
 * (browser/patches/0007-webgl-spoofing.patch). So only the limits both context
 * versions report identically, and that need no extension, are sent
 * (webglParameters).
 *
 * TypeScript twin of python/src/camouwolf/webgl.py.
 */
import { type TraceResult, trace } from "fpgen-js";
import { FPGEN_OS, isSoftwareRenderer, OS_KEYS } from "./fingerprints.js";
import { isPlainObject, KeyError, PyFloat, pyStrRepr } from "./pycompat.js";

/** The ANGLE backends Chrome uses on each OS, as they appear in the renderer. */
const BACKENDS: Readonly<Record<string, RegExp>> = {
	win: /Direct3D11|D3D11/i,
	mac: /Metal|OpenGL 4\.1/i,
	lin: /OpenGL|Mesa|Vulkan/i,
};

export type Scalar = number | PyFloat | bigint;

function osKey(targetOs: string): string {
	const lowered = targetOs.toLowerCase();
	return Object.hasOwn(OS_KEYS, lowered) ? OS_KEYS[lowered] : lowered;
}

function backend(targetOs: string): RegExp {
	const key = osKey(targetOs);
	if (!Object.hasOwn(BACKENDS, key)) throw new KeyError(pyStrRepr(key));
	return BACKENDS[key];
}

/**
 * Whether Chrome on `targetOs` ('win', 'mac', 'lin', or the OS name) can
 * report this renderer.
 */
export function backendMatchesOs(
	renderer: string | null | undefined,
	targetOs: string,
): boolean {
	if (!renderer) return true;
	return backend(targetOs).test(renderer);
}

/**
 * Whether an identity may claim fpgen's `gpu` value: a real device, on a
 * backend the OS uses.
 */
export function usableGpu(gpu: unknown, targetOs?: string | null): boolean {
	if (!isPlainObject(gpu) || !gpu.renderer || !gpu.vendor) return false;
	if (isSoftwareRenderer(gpu.renderer)) return false;
	return (
		targetOs === null ||
		targetOs === undefined ||
		backendMatchesOs(gpu.renderer, targetOs)
	);
}

/** The scalar value of one recorded parameter, or null. */
function comparable(entry: unknown): Scalar | null {
	if (!isPlainObject(entry) || entry.type !== "Number") return null;
	const value = entry.value;
	if (
		typeof value === "number" ||
		typeof value === "bigint" ||
		value instanceof PyFloat
	) {
		return value;
	}
	return null;
}

function sameScalar(a: Scalar | null, b: Scalar | null): boolean {
	if (a === null || b === null) return a === b;
	return Number(a) === Number(b);
}

/**
 * The `webGl:parameters` map of one recorded device: pname -> value.
 *
 * Kept: the numeric MAX_* limits of the WebGL1 core that the device's WebGL2
 * context reports with the same value. Left to the real GPU: state (which a
 * page changes and reads back), arrays (the key takes scalars), strings
 * (VERSION and SHADING_LANGUAGE_VERSION differ between WebGL1 and WebGL2),
 * extension limits (null until the extension is enabled) and anything the two
 * context versions report differently.
 */
export function webglParameters(
	webgl: unknown,
	webgl2: unknown,
): Record<string, Scalar> {
	const params = isPlainObject(webgl) ? webgl.params : null;
	if (!isPlainObject(params)) return {};
	const params2 = isPlainObject(webgl2) ? webgl2.params : null;
	const result: Record<string, Scalar> = {};
	for (const [pname, entry] of Object.entries(params)) {
		const value = comparable(entry);
		const name: unknown = isPlainObject(entry) ? (entry.name ?? "") : "";
		if (
			value === null ||
			typeof name !== "string" ||
			!name.startsWith("MAX_") ||
			name.endsWith("_EXT") ||
			name.endsWith("_WEBGL") ||
			name.endsWith("_OES")
		) {
			continue;
		}
		if (!/^\d+$/.test(pname)) continue;
		if (
			isPlainObject(params2) &&
			!sameScalar(comparable(params2[pname]), value)
		) {
			continue;
		}
		result[pname] = value;
	}
	return result;
}

const traceCache = new Map<string, readonly TraceResult[]>();

function traceGpus(targetOs: string): readonly TraceResult[] {
	let results = traceCache.get(targetOs);
	if (!results) {
		if (!Object.hasOwn(FPGEN_OS, targetOs))
			throw new KeyError(pyStrRepr(targetOs));
		results = trace("gpu", {
			browser: "Chrome",
			os: FPGEN_OS[targetOs],
		}) as TraceResult[];
		traceCache.set(targetOs, results);
	}
	return results;
}

/**
 * Every [vendor, renderer] fpgen has seen Chrome report on this OS, that an
 * identity may claim.
 *
 * A GPU outside this set has no recorded device behind it, so an identity
 * naming it could only borrow another machine's limits, screen and fonts.
 */
export function chromeGpus(targetOs: string): Array<[string, string]> {
	const key = osKey(targetOs);
	const seen = new Set<string>();
	const pairs: Array<[string, string]> = [];
	for (const result of traceGpus(key)) {
		if (!usableGpu(result.value, key)) continue;
		const id = JSON.stringify([result.value.vendor, result.value.renderer]);
		if (seen.has(id)) continue;
		seen.add(id);
		pairs.push([result.value.vendor, result.value.renderer]);
	}
	return pairs;
}
