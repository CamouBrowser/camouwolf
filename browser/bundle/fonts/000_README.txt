Bundled fonts — NOT committed.
==============================

Cross-OS font-fingerprint spoofing needs the actual font files for every OS we
claim to be. A Windows persona whose font list names "Segoe UI" but cannot
render it is caught by one measureText call, so the launcher only ever reports
fonts that are present here (see `bundledUnion` in src/utils.ts).

These files are large (~1.2GB) and copyrighted, so only this README and the
restore script are tracked. Restore them from the local cache before a packaging
build:

    bash browser/bundle/fonts/restore-fonts.sh

Override the cache location with CAMOUWOLF_FONT_CACHE. The cache is shared with Holo
— the font set is a property of the OPERATING SYSTEM, not of the browser, so
both projects want exactly the same bytes.

Without this bundle a build still works, but only for same-OS identities:
`fonts.config.ts` filters the reported list to what is present, so a foreign-OS
persona simply ends up with an implausibly thin font set. release-camouwolf.sh warns
when it packages without them.
