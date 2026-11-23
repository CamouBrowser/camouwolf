/**
 * WebGL identities, from the Chrome devices fpgen has recorded.
 *
 * Chrome reports its GPU through ANGLE, and the renderer string names the
 * ANGLE backend: Direct3D 11 on Windows, Metal (or OpenGL 4.1 on older Macs)
 * on macOS, desktop OpenGL or Vulkan on Linux. A renderer whose backend the
 * claimed OS never uses is a contradiction a page reads in one call, so every
 * identity's renderer is checked against its OS (backendMatchesOs).
 *
 * A device's WebGL identity is more than its renderer string: the limits, the
 * shader precision table and the extension lists are the backend's signature
 * (Direct3D 11 reports ALIASED_LINE_WIDTH_RANGE [1, 1] and MAX_VIEWPORT_DIMS
 * [32767, 32767] whatever the card), so they are claimed from the same record
 * (webglIdentity). The browser answers them before its own lookup
 * (browser/patches/0007-webgl-spoofing.patch); an extension it lacks cannot be
 * added, only one the claimed device lacks hidden.
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

// The GL spec's implementation-dependent values: fixed per device, never state
// a page sets. Extension limits are left out, because the browser answers a
// configured pname whether or not its extension is enabled.
const LIMIT_PREFIXES = ["MAX_", "MIN_", "ALIASED_"];
const LIMITS = new Set(["SUBPIXEL_BITS", "UNIFORM_BUFFER_OFFSET_ALIGNMENT"]);
const EXTENSION_SUFFIXES = ["_EXT", "_WEBGL", "_OES"];

function isNumber(value: unknown): value is Scalar {
	return (
		typeof value === "number" ||
		typeof value === "bigint" ||
		value instanceof PyFloat
	);
}

/** The value of one recorded parameter if it is a device limit, or null. */
function limit(entry: unknown): Scalar | Scalar[] | null {
	if (!isPlainObject(entry)) return null;
	const name: unknown = entry.name ?? "";
	if (
		typeof name !== "string" ||
		EXTENSION_SUFFIXES.some((suffix) => name.endsWith(suffix)) ||
		!(
			LIMIT_PREFIXES.some((prefix) => name.startsWith(prefix)) ||
			LIMITS.has(name)
		)
	) {
		return null;
	}
	const value: unknown = entry.value;
	if (entry.type === "Number" && isNumber(value)) {
		// fpgen-js parses an integer beyond 2**53 (MAX_SERVER_WAIT_TIMEOUT) into
		// a double; Python's fpgen keeps the recorded digits as an int.
		return typeof value === "number" &&
			Number.isInteger(value) &&
			!Number.isSafeInteger(value)
			? BigInt(String(value))
			: value;
	}
	if (
		(entry.type === "Float32Array" || entry.type === "Int32Array") &&
		Array.isArray(value) &&
		value.length >= 2 &&
		value.length <= 4 &&
		value.every(isNumber)
	) {
		return [...value];
	}
	return null;
}

/** The device limits one recorded context reports: pname -> value. */
export function webglParameters(
	context: unknown,
): Record<string, Scalar | Scalar[]> {
	const params = isPlainObject(context) ? context.params : null;
	if (!isPlainObject(params)) return {};
	const result: Record<string, Scalar | Scalar[]> = {};
	for (const [pname, entry] of Object.entries(params)) {
		const value = limit(entry);
		if (value !== null && /^\d+$/.test(pname)) result[pname] = value;
	}
	return result;
}

type PrecisionFormat = {
	rangeMin: number;
	rangeMax: number;
	precision: number;
};

/** A recorded getShaderPrecisionFormat table, keyed "<shaderType>,<precisionType>". */
export function shaderPrecisionFormats(
	context: unknown,
): Record<string, PrecisionFormat> {
	const formats = isPlainObject(context)
		? context.shaderPrecisionFormats
		: null;
	const table: Record<string, PrecisionFormat> = {};
	for (const item of Array.isArray(formats) ? formats : []) {
		const spf = isPlainObject(item) ? item.shaderPrecisionFormat : null;
		if (
			isPlainObject(spf) &&
			["rangeMin", "rangeMax", "precision"].every((k) =>
				Number.isInteger(spf[k]),
			)
		) {
			table[`${item.shaderType},${item.precisionType}`] = {
				rangeMin: spf.rangeMin,
				rangeMax: spf.rangeMax,
				precision: spf.precision,
			};
		}
	}
	return table;
}

/**
 * The webGl:* and webGl2:* keys of the record's WebGL1 and WebGL2 contexts.
 *
 * Both are claimed or neither: fpgen occasionally pairs one device's GPU with
 * another's WebGL readout, whose limits would contradict the claimed renderer,
 * and a WebGL2 context without its own map answers from the WebGL1 one.
 */
export function webglIdentity(
	fingerprint: Record<string, any>,
	renderer: unknown,
): Record<string, unknown> {
	const { webgl, webgl2 } = fingerprint;
	if (
		!renderer ||
		![webgl, webgl2].every((r) => isPlainObject(r) && r.renderer === renderer)
	) {
		return {};
	}
	const claimed: Record<string, unknown> = {
		"webGl:parameters": webglParameters(webgl),
		"webGl2:parameters": webglParameters(webgl2),
		"webGl:shaderPrecisionFormats": shaderPrecisionFormats(webgl),
		"webGl2:shaderPrecisionFormats": shaderPrecisionFormats(webgl2),
		"webGl:supportedExtensions": webgl.supportedExtensions,
		"webGl2:supportedExtensions": webgl2.supportedExtensions,
	};
	return Object.fromEntries(
		Object.entries(claimed).filter(([, value]) =>
			Array.isArray(value)
				? value.length > 0
				: isPlainObject(value) && Object.keys(value).length > 0,
		),
	);
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
