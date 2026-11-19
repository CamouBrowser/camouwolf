/**
 * Platform constants, install paths and the bundled data files. A leaf of the
 * module graph: pkgman.ts and multiversion.ts import each other, and both need
 * these values while they are still being evaluated.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { setModelDir } from "fpgen-js";
import { parse as parseYaml } from "yaml";
import { PRODUCT } from "./product.js";

export { rprint, style } from "camoucore/download.js";
export { ARCH_MAP, OS_MAP, OS_NAME } from "camoucore/host.js";

export const INSTALL_DIR: string = PRODUCT.cacheDir;

/**
 * The data files both launchers read. They live in python/src/camouwolf/, the one
 * copy in the repo; `pnpm build` copies them into dist/data-files/ for the npm
 * tarball, so one seed draws one identity in either launcher.
 */
export const DATA_FILES: readonly string[] = [
	"browser-pin.json",
	"data/properties.json",
	"data/system-colors.json",
	"data/ubo.json",
	"fpgen.yml",
	"launcher-constants.json",
	"repos.yml",
	"warnings.yml",
];

/**
 * Where DATA_FILES are read from: dist/data-files/ in a built package, else
 * python/src/camouwolf/ itself when running from src/ in the repo.
 */
export const LOCAL_DATA: string = fs.existsSync(
	path.join(import.meta.dirname, "data-files"),
)
	? path.join(import.meta.dirname, "data-files")
	: path.resolve(import.meta.dirname, "..", "..", "python", "src", "camouwolf");

/** A data file (relative to LOCAL_DATA), parsed as YAML or JSON by its extension. */
export function loadDataFile<T = any>(file: string): T {
	const text = fs.readFileSync(path.resolve(LOCAL_DATA, file), "utf-8");
	return (file.endsWith(".yml") ? parseYaml(text) : JSON.parse(text)) as T;
}

/** Values both launchers use, kept in one file (python/src/camouwolf/launcher-constants.json). */
export interface LauncherConstants {
	plausibleCoreCounts: number[];
	appleSiliconCores: number[];
	intelMacIgpCores: number[];
	intelMacDgpuExtraCores: number[];
	appleSiliconPanels: Record<string, [number, number][]>;
	taskbarHeight: Record<"win" | "mac" | "lin", number>;
	plausibleDpr: Record<"win" | "mac" | "lin", number[]>;
	essentialFonts: Record<"win" | "mac" | "lin", string[]>;
}
export const LAUNCHER_CONSTANTS: Readonly<LauncherConstants> = loadDataFile(
	"launcher-constants.json",
);

// Camouwolf keeps the fpgen model in its cache, beside the browsers.
setModelDir(
	process.env.CAMOUWOLF_FPGEN_DATA || path.join(INSTALL_DIR, "fpgen"),
);

export const OS_ARCH_MATRIX: Record<string, string[]> = {
	win: ["x86_64", "i686"],
	mac: ["x86_64", "arm64"],
	lin: ["x86_64", "arm64", "i686"],
};

export const LAUNCH_FILE: Record<string, string> = {
	win: "camouwolf.exe",
	mac: "../MacOS/camouwolf",
	lin: "camouwolf",
};
