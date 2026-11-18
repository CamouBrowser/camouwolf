/**
 * Extensions: the caller's unpacked ones, and the default uBlock Origin Lite.
 *
 * Chromium has no Manifest V2, so the default ad blocker is uBlock Origin Lite
 * (Manifest V3) where Camoufox ships uBlock Origin. It is not vendored and not
 * part of the browser bundle: `camouwolf fetch` downloads the release pinned in
 * data/ubo.json (version, url, sha256) and unpacks it under the cache directory.
 *
 * TypeScript twin of python/src/camouwolf/addons.py.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { InvalidAddonPath } from "./exceptions.js";
import { INSTALL_DIR, loadDataFile } from "./paths.js";
import { unzip, verifySha256, webdl } from "./pkgman.js";
import { loadWarnings, warn } from "./warnings.js";

/** Injection points (tests point the addon directory at a fixture). */
export const addonsDeps = {
	addonsDir: path.join(INSTALL_DIR, "addons"),
};

/**
 * Default addons. `camouwolf fetch` installs them, unpacked, under
 * <cache>/addons/<name>/.
 */
export const DefaultAddons = {
	UBO: "UBO",
} as const;

export type DefaultAddon = keyof typeof DefaultAddons;

/** name -> the pin file under data/ that says what to download. */
export const ADDON_PINS: Readonly<Record<DefaultAddon, string>> = {
	UBO: "ubo.json",
};

export interface AddonPin {
	name: string;
	version: string;
	url: string;
	sha256: string;
}

/** The pinned release of a default addon: name, version, url, sha256. */
export function addonPin(addon: DefaultAddon): AddonPin {
	return loadDataFile<AddonPin>(`data/${ADDON_PINS[addon]}`);
}

/**
 * Confirms that the addon paths are valid.
 *
 * Chromium treats an extension it cannot load as a warning on stderr and
 * starts without it, so a mistyped path would otherwise launch a browser that
 * just lacks the extension. --load-extension takes an unpacked directory; a
 * .crx is ignored.
 */
export function confirmPaths(paths: string[]): void {
	for (const addonPath of paths) {
		if (!fs.existsSync(addonPath) || !fs.statSync(addonPath).isDirectory()) {
			throw new InvalidAddonPath(
				`${addonPath} is not a directory. Addons are unpacked extension directories; ` +
					"a .crx or .zip is not loaded.",
			);
		}
		if (!fs.existsSync(path.join(addonPath, "manifest.json"))) {
			throw new InvalidAddonPath(
				`${addonPath}: manifest.json is missing. Addon path must be a path to an extracted addon.`,
			);
		}
	}
}

/** Returns the path a default addon is installed at. */
export function getAddonPath(addonName: string): string {
	return path.join(addonsDeps.addonsDir, addonName);
}

/** Whether the pinned version of a default addon is installed. */
export function isInstalled(addon: DefaultAddon): boolean {
	const dir = getAddonPath(addon);
	let installed: any;
	try {
		installed = JSON.parse(
			fs.readFileSync(path.join(dir, ".camouwolf-addon.json"), "utf8"),
		);
	} catch {
		return false;
	}
	return (
		fs.existsSync(path.join(dir, "manifest.json")) &&
		installed?.sha256 === addonPin(addon).sha256
	);
}

/** Download, verify and unpack every default addon. Returns their paths. */
export async function fetchDefaultAddons(replace = false): Promise<string[]> {
	const paths: string[] = [];
	for (const addon of Object.keys(DefaultAddons) as DefaultAddon[]) {
		const dir = getAddonPath(addon);
		if (replace || !isInstalled(addon)) {
			const pin = addonPin(addon);
			const archive = await webdl(
				pin.url,
				`Downloading ${pin.name} ${pin.version}`,
			);
			verifySha256(archive, pin.sha256, pin.name);
			const staging = `${dir}.new`;
			fs.rmSync(staging, { recursive: true, force: true });
			fs.mkdirSync(staging, { recursive: true });
			// Verified against the pinned sha256.
			unzip(archive, staging, undefined, false);
			fs.writeFileSync(
				path.join(staging, ".camouwolf-addon.json"),
				JSON.stringify({ version: pin.version, sha256: pin.sha256 }),
			);
			fs.rmSync(dir, { recursive: true, force: true });
			fs.renameSync(staging, dir);
		}
		paths.push(dir);
	}
	return paths;
}

/**
 * Adds the installed default addons, minus any specified in excludeList, to
 * addonsList.
 */
export function addDefaultAddons(
	addonsList: string[],
	excludeList?: readonly DefaultAddon[] | null,
): void {
	for (const name of Object.keys(DefaultAddons) as DefaultAddon[]) {
		if (excludeList?.includes(name)) continue;
		const addonPath = getAddonPath(name);
		// Installed by `camouwolf fetch`. Without it the browser still
		// launches, but not as the browser asked for.
		if (!fs.existsSync(path.join(addonPath, "manifest.json"))) {
			warn(
				loadWarnings()
					.addon_not_installed.replaceAll("{addon}", name)
					.replaceAll("{path}", addonPath),
				"RuntimeWarning",
			);
			continue;
		}
		addonsList.push(addonPath);
	}
}
