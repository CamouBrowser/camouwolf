/**
 * What this machine's own GPU reports through the browser.
 *
 * A library probe, not part of the launch: Camouwolf's identity names its GPU
 * in webGl:vendor / webGl:renderer, which the browser reports whatever the
 * host renders on, so a launch never needs to know the host's device. The
 * probe answers the question a caller choosing between headless modes has:
 * does this display render on hardware, or on SwiftShader?
 *
 *     import { hostGpu, rendersOnHardware } from "camouwolf";
 *     const gpu = await hostGpu(executablePath, true, process.env, []);
 *
 * TypeScript twin of python/src/camouwolf/host_rendering.py. Python runs the
 * probe in a child interpreter (its sync API cannot start inside a running
 * loop); Node has no such limit, so the probe runs in-process under the same
 * deadline.
 */
import { chromium } from "playwright-core";
import { isSoftwareRenderer } from "./fingerprints.js";

/** A GPU as WEBGL_debug_renderer_info reports it: [vendor, renderer]. */
export type Gpu = [string, string];

// What the launcher adds to the environment, which the probe leaves out: it
// reads the device, not an identity's claim about it.
const LAUNCHER_ENV = ["CAMOU_", "CAMOUWOLF_UCRT_MATH", "FONTCONFIG_FILE"];

/**
 * A display that accepts TCP but never completes the X handshake leaves the
 * browser's launch waiting forever; the probe is given this long.
 */
export const HOST_GPU_PROBE_TIMEOUT_MS = 30_000;
const HOST_GPU_CLOSE_TIMEOUT_MS = 1_000;

const probes = new Map<string, Promise<Gpu | null>>();

function within<T>(
	promise: Promise<T>,
	timeoutMs: number,
	message: string,
): Promise<T> {
	return new Promise<T>((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error(message)), timeoutMs);
		timer.unref();
		promise.then(
			(value) => {
				clearTimeout(timer);
				resolve(value);
			},
			(error) => {
				clearTimeout(timer);
				reject(error);
			},
		);
	});
}

/**
 * The [vendor, renderer] the browser reports on this machine with no identity
 * applied, or null without WebGL.
 *
 * Read under the given display and switches: headless, Xvfb, a GPU display and
 * a real display can each render on a different device, and so can a launch
 * with gpudisplay.GPU_SWITCHES. The strings are ANGLE's.
 */
export function hostGpu(
	executablePath: string,
	headless: boolean,
	env: Record<string, string | number | boolean | undefined>,
	args: readonly string[] = [],
): Promise<Gpu | null> {
	const hostEnv = Object.entries(env)
		.filter(
			([key, value]) =>
				value !== undefined &&
				!LAUNCHER_ENV.some((prefix) => key.startsWith(prefix)),
		)
		.map(([key, value]) => [key, String(value)])
		.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
	const key = JSON.stringify([executablePath, headless, hostEnv, args]);
	let gpu = probes.get(key);
	if (!gpu) {
		gpu = readHostGpu(executablePath, headless, Object.fromEntries(hostEnv), [
			...args,
		]);
		// Remember a reading, not a failure: the next call tries again.
		gpu.catch(() => probes.delete(key));
		probes.set(key, gpu);
	}
	return gpu;
}

/** Launch the binary once and read its WebGL vendor and renderer. */
async function readHostGpu(
	executablePath: string,
	headless: boolean,
	env: Record<string, string>,
	args: string[],
): Promise<Gpu | null> {
	const deadline = Date.now() + HOST_GPU_PROBE_TIMEOUT_MS;
	const display = env.DISPLAY ? ` (DISPLAY=${env.DISPLAY})` : "";
	const timeoutMessage =
		`Host GPU probe timed out after ${HOST_GPU_PROBE_TIMEOUT_MS / 1000} s ` +
		`while launching ${executablePath}${display}. The browser did not ` +
		"answer the WebGL probe; check that the display accepts X connections.";
	const remaining = () => Math.max(1, deadline - Date.now());
	let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined;
	try {
		browser = await within(
			chromium.launch({
				executablePath,
				headless,
				env,
				args,
				timeout: remaining(),
			}),
			remaining(),
			timeoutMessage,
		);
		const page = await within(browser.newPage(), remaining(), timeoutMessage);
		return await within(
			page.evaluate((): Gpu | null => {
				const gl = (globalThis as any).document
					.createElement("canvas")
					.getContext("webgl");
				const ext = gl?.getExtension("WEBGL_debug_renderer_info");
				if (!ext) return null;
				return [
					gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
					gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
				];
			}),
			remaining(),
			timeoutMessage,
		);
	} catch (error) {
		if (error instanceof Error && error.message === timeoutMessage) throw error;
		throw new Error(
			`Host GPU probe failed while launching ${executablePath}:\n${error instanceof Error ? error.message : String(error)}`,
			{ cause: error },
		);
	} finally {
		if (browser) {
			await within(
				browser.close(),
				HOST_GPU_CLOSE_TIMEOUT_MS,
				"close timed out",
			).catch(() => {});
		}
	}
}

/** Whether the host's canvas and WebGL come from a GPU, not a software rasterizer. */
export function rendersOnHardware(gpu: Gpu | null): gpu is Gpu {
	return gpu !== null && !isSoftwareRenderer(gpu[1]);
}
