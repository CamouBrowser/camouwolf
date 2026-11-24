Bundled fonts — NOT committed.
==============================

Cross-OS font spoofing needs the actual font files for every OS an identity
can claim. A Windows identity whose font list names "Segoe UI" but cannot
render it is caught by one measureText call.

camouwolf ships camoufox's font bundle, byte for byte: the font set belongs to
the OS being claimed, not to the browser claiming it.
browser/scripts/data/font-bundle.json pins the release asset and its sha256
(a copy of camoufox's own pin). Fetch and unpack it into bundle/fonts/ with:

    python3 browser/scripts/fetch-fonts.py --extract

The archive is cached in CAMOUWOLF_FONT_CACHE (default ~/.cache/camouwolf/fonts)
and the download is capped by CAMOUWOLF_FONT_RATE (default 10M per second).

The bundle is split into groups by which OSes read each file (L, LM, LMW, LW,
M, MW, W); fonts/groups.json maps each OS to its groups, and the launcher
points a persona's fontconfig (bundle/fontconfig/<os>/fonts.conf) at those
groups only.

The launcher claims only families the persona can resolve from this bundle:
python/src/camouwolf/data/bundle-fonts.json, written by
scripts/gen-bundle-fonts.py. After the pin changes, re-extract and regenerate
it; `python3 -m ci.run_patch_guards --only bundle-fonts` fails until you do,
and also when camoufox moves its pin.

release-camouwolf.sh refuses to package without the pinned bundle.
