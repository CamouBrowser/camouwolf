// Copy DATA_FILES (src/paths.ts) from python/src/camouwolf/ into dist/data-files/.
// Runs after tsc, so the list comes from the compiled paths.js. The Python
// package holds the one copy of each file; tests/data-files.test.ts fails when
// a copy differs from it.
//
//   node scripts/copy-files.mjs [target-dir]
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON_DATA = path.resolve(ROOT, "..", "python", "src", "camouwolf");
const DATA = path.resolve(
	process.argv[2] ?? path.join(ROOT, "dist", "data-files"),
);

fs.rmSync(DATA, { recursive: true, force: true });
const { DATA_FILES } = await import(path.join(ROOT, "dist", "paths.js"));
for (const name of DATA_FILES) {
	fs.mkdirSync(path.dirname(path.join(DATA, name)), { recursive: true });
	fs.copyFileSync(path.join(PYTHON_DATA, name), path.join(DATA, name));
}
console.log(
	`copied ${DATA_FILES.length} data files from python/src/camouwolf -> ${path.relative(ROOT, DATA)}`,
);
