/**
 * Probe the surface patch 0018 (ucrt-math) actually touches.
 *
 * 0018 does NOT redirect JS `Math.sin` — V8 implements that with its own fdlibm
 * port, which is bit-identical on every platform, so a JS-level comparison says
 * nothing about the patch. What 0018 redirects is the C++ trig behind
 * gfx::SinCosDegrees, DOMMatrix and AffineTransform: CSS rotations, matrix
 * readback, and anything that lands in a computed transform. Those go through
 * the platform libm, which is UCRT on Windows and glibc on Linux — and THAT is
 * where a divergence, if there is one, would show up.
 *
 * Run: node tests/compare/probe-geometry-math.mjs --mode cdp|local [...]
 */
import fs from "node:fs";
import path from "node:path";

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf(n); return i >= 0 && argv[i + 1] ? argv[i + 1] : d; };

export const GEOMETRY_MATH = () => {
	const out = {};
	// Angles chosen to land in every branch of gfx::SinCosDegrees: the exact
	// quadrant boundaries it special-cases, the 45..135 fold, and the fmod slow
	// path for extreme inputs.
	const angles = [0, 1, 7.3, 30, 45, 60, 89.9999, 90, 123.456, 135, 180,
		270, 359.999, 360, 1e5 + 0.5, -1e7 + 0.25, 1e15 + 1];

	out.domMatrixRotate = angles.map((a) => {
		const m = new DOMMatrix().rotate(a);
		return [a, m.a, m.b, m.c, m.d];
	});
	out.domMatrixRotateAxisAngle = angles.map((a) => {
		const m = new DOMMatrix().rotateAxisAngle(0.3, 0.5, 0.81, a);
		return [a, m.m11, m.m12, m.m13, m.m21, m.m22, m.m23, m.m31, m.m32, m.m33];
	});
	out.domMatrixRotateFromVector = [[1, 2], [-3, 7], [1e-9, 1], [5, -0.0001]].map(
		([x, y]) => {
			const m = new DOMMatrix().rotateFromVector(x, y);
			return [x, y, m.a, m.b, m.c, m.d];
		});
	out.domMatrixSkew = [1, 22.5, 44.9, 89.9].map((a) => {
		const m = new DOMMatrix().skewX(a).skewY(a / 2);
		return [a, m.a, m.b, m.c, m.d];
	});

	// The CSS path: a computed transform is produced by AffineTransform/gfx and
	// serialised back as a matrix, so it exercises the same C++ trig from a
	// completely different entry point.
	const el = document.createElement("div");
	el.style.cssText = "position:absolute;width:100px;height:100px";
	document.body.appendChild(el);
	out.cssTransform = angles.map((a) => {
		el.style.transform = `rotate(${a}deg)`;
		return [a, getComputedStyle(el).transform];
	});
	out.cssTransform3d = [17.3, 45, 91.7].map((a) => {
		el.style.transform = `rotate3d(0.3,0.5,0.81,${a}deg) skewX(${a / 3}deg)`;
		return [a, getComputedStyle(el).transform];
	});
	// A rotated element's layout box: gfx::SinCosDegrees feeds the mapped rect,
	// so the rounded rect is a second, independent witness of the same trig.
	out.rotatedRects = angles.map((a) => {
		el.style.transform = `rotate(${a}deg)`;
		const r = el.getBoundingClientRect();
		return [a, r.width, r.height];
	});
	el.remove();

	// SVG has its own matrix stack; it also lands in AffineTransform.
	const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
	document.body.appendChild(svg);
	out.svgMatrix = angles.map((a) => {
		const m = svg.createSVGMatrix().rotate(a);
		return [a, m.a, m.b, m.c, m.d];
	});
	svg.remove();

	// Canvas 2D transforms go through the same geometry code.
	const ctx = document.createElement("canvas").getContext("2d");
	out.canvasTransform = angles.map((a) => {
		ctx.setTransform(1, 0, 0, 1, 0, 0);
		ctx.rotate((a * Math.PI) / 180);
		const t = ctx.getTransform();
		return [a, t.a, t.b, t.c, t.d];
	});
	return out;
};

if (import.meta.url === `file://${process.argv[1]}`) {
	const { chromium } = await import("playwright-core");
	const mode = arg("--mode", "cdp");
	let browser;
	if (mode === "cdp") browser = await chromium.connectOverCDP(arg("--cdp", "http://127.0.0.1:9222"));
	else browser = await chromium.launch({ executablePath: arg("--exec"), headless: false,
		args: ["--no-first-run", "--no-default-browser-check", "--disable-search-engine-choice-screen"] });
	const ctx = browser.contexts()[0] || (await browser.newContext());
	const page = await ctx.newPage();
	await page.waitForLoadState("domcontentloaded").catch(() => {});
	const r = await page.evaluate(GEOMETRY_MATH);
	const out = arg("--out");
	const json = `${JSON.stringify(r, null, "\t")}\n`;
	if (out) { fs.mkdirSync(path.dirname(out), { recursive: true }); fs.writeFileSync(out, json); console.error(`wrote ${out}`); }
	else process.stdout.write(json);
	await browser.close();
}
