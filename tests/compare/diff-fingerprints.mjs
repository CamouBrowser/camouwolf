/**
 * Deep-diff two fingerprint captures.
 *
 * Every leaf that differs is a place a detector can tell the two browsers
 * apart. The output is deliberately flat and path-addressed so it can be sorted,
 * counted, and pasted into an issue — a nested diff of two 45 KB documents is
 * unreadable and, worse, easy to skim past.
 *
 * Run: node tests/compare/diff-fingerprints.mjs A.json B.json [--only prefix] [--json]
 */
import fs from "node:fs";

const argv = process.argv.slice(2);
const only = argv.includes("--only") ? argv[argv.indexOf("--only") + 1] : null;
// Drop flags AND the value that belongs to --only, or the prefix gets treated
// as a third input file and the tool prints usage instead of the diff.
const files = argv.filter((a, i) => !a.startsWith("--") && argv[i - 1] !== "--only");
const asJson = argv.includes("--json");
const showSame = argv.includes("--same");
if (files.length !== 2) {
	console.error("usage: diff-fingerprints.mjs A.json B.json [--only prefix] [--json]");
	process.exit(2);
}

const [A, B] = files.map((f) => JSON.parse(fs.readFileSync(f, "utf8")));
const nameA = A.__meta?.label ?? files[0];
const nameB = B.__meta?.label ?? files[1];

/** Flatten to dotted paths. Arrays of scalars stay whole — order matters and a
 *  per-index diff of a 40-entry extension list is noise, not signal. */
function flatten(obj, prefix = "", out = {}) {
	if (obj === null || typeof obj !== "object") { out[prefix] = obj; return out; }
	if (Array.isArray(obj)) {
		const scalar = obj.every((v) => v === null || typeof v !== "object");
		if (scalar) { out[prefix] = obj; return out; }
		obj.forEach((v, i) => flatten(v, `${prefix}[${i}]`, out));
		if (obj.length === 0) out[prefix] = [];
		return out;
	}
	const keys = Object.keys(obj);
	if (keys.length === 0) { out[prefix] = {}; return out; }
	for (const k of keys) flatten(obj[k], prefix ? `${prefix}.${k}` : k, out);
	return out;
}

const fa = flatten(A), fb = flatten(B);
const paths = [...new Set([...Object.keys(fa), ...Object.keys(fb)])].sort();
const eq = (x, y) => JSON.stringify(x) === JSON.stringify(y);
const show = (v) => {
	if (v === undefined) return "—absent—";
	const s = JSON.stringify(v);
	return s.length > 220 ? `${s.slice(0, 217)}...` : s;
};

const diffs = [];
for (const p of paths) {
	if (p.startsWith("__meta")) continue;
	if (only && !p.startsWith(only)) continue;
	if (eq(fa[p], fb[p])) { if (showSame) diffs.push({ path: p, same: true, value: fa[p] }); continue; }
	diffs.push({ path: p, a: fa[p], b: fb[p] });
}

if (asJson) {
	console.log(JSON.stringify({ a: nameA, b: nameB, diffs }, null, "\t"));
} else {
	const differing = diffs.filter((d) => !d.same);
	console.log(`# ${nameA}  vs  ${nameB}`);
	console.log(`# ${differing.length} differing leaves of ${paths.length} compared\n`);
	let group = null;
	for (const d of diffs) {
		const g = d.path.split(/[.[]/)[0];
		if (g !== group) { group = g; console.log(`\n## ${group}`); }
		if (d.same) { console.log(`  =  ${d.path} = ${show(d.value)}`); continue; }
		console.log(`  ! ${d.path}`);
		console.log(`      A: ${show(d.a)}`);
		console.log(`      B: ${show(d.b)}`);
	}
}
