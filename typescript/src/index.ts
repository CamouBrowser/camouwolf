/**
 * Public API. Mirrors python/src/camouwolf/__init__.py (Camouwolf, NewBrowser,
 * NewContext, their Async* twins, DefaultAddons, launch_options), plus the
 * package-management and server helpers the TS port has always exported.
 */
import "./node-floor.js";

export { attach } from "./_humanize_playwright.js";
export type { DefaultAddon } from "./addons.js";
export { DefaultAddons } from "./addons.js";
export {
	AsyncCamouwolf,
	AsyncNewBrowser,
	AsyncNewContext,
} from "./async_api.js";
export * from "./exceptions.js";
export { Screen } from "./fingerprints.js";
export {
	cursory,
	type HumanizeSetting,
	type MyuriOptions,
	myuri,
	raw,
} from "./humanize.js";
export {
	findInstalledVersion,
	listInstalled,
	printTree,
} from "./multiversion.js";
export { INSTALL_DIR, OS_NAME } from "./paths.js";
export { CamouwolfFetcher, installedVerStr, RepoConfig } from "./pkgman.js";
export { type LaunchServerOptions, launchServer } from "./server.js";
export {
	Camouwolf,
	type Headless,
	NewBrowser,
	type NewBrowserOptions,
	NewContext,
	type NewContextOptions,
} from "./sync_api.js";
export {
	type LaunchOptions,
	launchOptions,
	launchOptions as launch_options,
} from "./utils.js";
export { VirtualDisplay } from "./virtdisplay.js";
export { FallbackWarning, LeakWarning } from "./warnings.js";
