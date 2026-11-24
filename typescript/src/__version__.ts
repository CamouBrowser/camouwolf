/**
 * Camouwolf version constants.
 *
 * TypeScript twin of python/src/camouwolf/__version__.py.
 */

// biome-ignore lint/complexity/noStaticOnlyClass: mirrors the Python twin's CONSTRAINTS class so both launchers read the same
export class CONSTRAINTS {
	/**
	 * The minimum and maximum supported versions of the Camouwolf browser.
	 */
	static readonly MIN_VERSION: string = "alpha.1";
	static readonly MAX_VERSION: string = "1";

	/**
	 * [playwrightVersion, browserBuild]: from that Playwright on, the browser must
	 * be at least that build. Empty: Camouwolf is driven over CDP, which no
	 * Playwright release has yet outgrown for a Camouwolf build.
	 */
	static readonly PLAYWRIGHT_BROWSER_FLOORS: ReadonlyArray<
		readonly [readonly number[], string]
	> = [];

	/**
	 * The browser interface built from this tree. Every browser release
	 * declares it in its manifest.json (ci/release.py reads it from the Python
	 * twin), and this library accepts browsers from MIN_INTERFACE up to it.
	 * Raise it when a browser change would break released libraries; raise
	 * MIN_INTERFACE when this library can no longer drive older browsers.
	 * Releases from before manifests carried the field are interface 1.
	 */
	static readonly INTERFACE: number = 1;
	static readonly MIN_INTERFACE: number = 1;
}

/** Version of this launcher library. Kept in step with package.json and
 *  pythonlib's pyproject.toml. */
export const LIBRARY_VERSION = "0.1.0";
