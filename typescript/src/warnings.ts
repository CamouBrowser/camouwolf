/**
 * Camouwolf's warning messages (warnings.yml) and FallbackWarning.
 *
 * The channel itself (warn, recordWarnings, LeakWarning) is camoucore's, so
 * one recorder catches what the core and this launcher emit.
 */
import * as os from "node:os";
import { warn } from "camoucore/warnings.js";
import { LIBRARY_VERSION } from "./__version__.js";
import { CamouwolfNotInstalled } from "./exceptions.js";
import { loadDataFile } from "./paths.js";
import { installedVerStr } from "./pkgman.js";

export {
	LeakWarning,
	type RecordedWarning,
	recordWarnings,
	warn,
} from "camoucore/warnings.js";

let warningsData: Record<string, string> | undefined;

/** warnings.yml, loaded once. */
export function loadWarnings(): Record<string, string> {
	warningsData ??= loadDataFile<Record<string, string>>("warnings.yml");
	return warningsData;
}

function browserVersion(): string {
	try {
		return installedVerStr();
	} catch (error) {
		if (error instanceof CamouwolfNotInstalled) return "not installed";
		throw error;
	}
}

/**
 * Emitted when part of an identity could not be drawn and a substitute was used.
 */
export class FallbackWarning extends Error {
	constructor(message: string) {
		super(message);
		this.name = "FallbackWarning";
	}

	/**
	 * Warns that `what` failed with `error` and the identity uses `instead`,
	 * with a block of versions and the error for the user to paste into an issue.
	 */
	static warn(
		what: string,
		instead: string,
		error: unknown,
		identity?: string | null,
	): void {
		const lines = [
			`camouwolf: ${LIBRARY_VERSION} (npm)`,
			`browser: ${browserVersion()}`,
			`os: ${os.type()}-${os.release()}-${os.arch()}`,
			`node: ${process.versions.node}`,
			`error: ${error instanceof Error ? `${error.name}: ${error.message}` : String(error)}`,
		];
		if (identity) lines.push(`identity: ${identity}`);
		const values: Record<string, string> = {
			what,
			instead,
			report: lines.map((line) => `    ${line}`).join("\n"),
		};
		warn(
			loadWarnings().fallback.replace(
				/\{(what|instead|report)\}/g,
				(_, key: string) => values[key],
			),
			"FallbackWarning",
		);
	}
}
