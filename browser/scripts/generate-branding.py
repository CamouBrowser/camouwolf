#!/usr/bin/env python3
"""Render every Camouwolf icon in the tree from the one canonical vector.

Single source of truth:

    browser/assets/CamouwolfLogo.svg     (drawn by hand; edit it directly)

Two destinations, both committed:

    browser/branding/                mirrors Chromium's chrome/app/theme/ layout.
                                     apply-patches.sh copies this over the
                                     upstream art before the build, so the
                                     window icon, the task-switcher icon and
                                     chrome://-page logos are Camouwolf's.

    browser/settings/linux-desktop/icons/
                                     the hicolor theme icons install-desktop.sh
                                     drops into ~/.local/share/icons, which is
                                     what GNOME/KDE show for the launcher and
                                     the dock.

Both are generated here, together, for the reason Camoufox learned the hard way:
they are consumed by completely different machinery (grit vs. the XDG icon
theme), so nothing fails when one of them is refreshed and the other is not --
the launcher icon just quietly stays a crop of last year's logo. Single-sourcing
them from the same SVG in the same script is what stops that.

Which of these files actually reach a Linux `is_chrome_branded=false` build --
verified against the pinned tag's theme_resources.grd, chrome_unscaled_resources
.grd and installer/linux/BUILD.gn, not from memory:

  * chrome_scaled_image entries resolve under default_{100,200}_percent/, NOT
    under chromium/ -- which is why chrome/app/theme/chromium/ has no
    product_logo_32.png and why putting one there would do nothing.
  * Linux takes the chromium/linux/ variants for 16/24/48/64/128/256; the
    non-linux paths are kept in sync anyway so a future macOS/Windows target
    does not ship a half-rebranded tree.
  * product_logo_name_22*.png are deliberately NOT generated: they are the
    horizontal "Chromium" wordmark, not the mark, and squeezing a portrait
    silhouette into a 22px-tall name plate produces mush. See the README note.

Usage:
    python3 browser/scripts/generate-branding.py           # write everything
    python3 browser/scripts/generate-branding.py --check    # verify, no writes

--check compares rendered PIXELS within a small tolerance rather than file
bytes, so a different cairosvg/Pillow build on a CI runner does not fail the
gate over a byte of PNG filtering while still catching a logo that actually
moved.

Requires: cairosvg, pillow, numpy  (pip install cairosvg pillow numpy)
"""

import argparse
import io
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BROWSER_DIR = SCRIPT_DIR.parent
SOURCE_SVG = BROWSER_DIR / "assets" / "CamouwolfLogo.svg"
BRANDING_DIR = BROWSER_DIR / "branding"
DESKTOP_ICON_DIR = BROWSER_DIR / "settings" / "linux-desktop" / "icons"

# Fraction of each edge left empty. The mark's viewBox is square and the wolf
# nearly fills it, so this is the only breathing room there is. Enough that the
# ear tips do not touch the edge, small enough that the mark still reads at
# 32px.
MARGIN = 0.03

# Hicolor sizes install-desktop.sh installs. Keep in sync with ICON_SIZES there.
DESKTOP_ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)

# Sizes packed into the Windows .ico and the macOS .icns.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# The XPM's C symbol name, matching the upstream file it replaces. Nothing in
# the tree still #includes it, but a drop-in replacement should stay drop-in.
XPM_SYMBOL = "linux_icon_chromium_32_xpm"

# The mark's one flat fill. Asserted against the SVG at run time rather than
# trusted, so a recoloured logo cannot leave the XPM and mono icons behind.
FLAT_FILL = "#7A8CB4"

# Every PNG in the branding tree, as {path relative to chrome/app/theme/: size}.
BRANDING_PNGS = {
    # Unscaled resources, and what the Linux installer copies into hicolor.
    "chromium/product_logo_16.png": 16,
    "chromium/product_logo_24.png": 24,
    "chromium/product_logo_48.png": 48,
    "chromium/product_logo_64.png": 64,
    "chromium/product_logo_128.png": 128,
    "chromium/product_logo_256.png": 256,
    # IDR_STATUS_TRAY_ICON (macOS only). The mark is already one flat colour,
    # so it is "mono" as-is.
    "chromium/product_logo_22_mono.png": 22,
    # is_linux takes these for IDR_PRODUCT_LOGO_{64,128,256} and the installer.
    "chromium/linux/product_logo_24.png": 24,
    "chromium/linux/product_logo_48.png": 48,
    "chromium/linux/product_logo_64.png": 64,
    "chromium/linux/product_logo_128.png": 128,
    "chromium/linux/product_logo_256.png": 256,
    # chrome_scaled_image: IDR_PRODUCT_LOGO_32 and IDR_PRODUCT_LOGO_16. The
    # 200_percent copies are the same art at twice the pixels.
    "default_100_percent/chromium/product_logo_16.png": 16,
    "default_100_percent/chromium/product_logo_32.png": 32,
    "default_200_percent/chromium/product_logo_16.png": 32,
    "default_200_percent/chromium/product_logo_32.png": 64,
    "default_100_percent/chromium/linux/product_logo_16.png": 16,
    "default_100_percent/chromium/linux/product_logo_32.png": 32,
    # Upstream has no default_200_percent/chromium/linux/. Adding it is a
    # strict upgrade: grit prefers real 2x art over upscaling the 1x, and
    # fallback_to_low_resolution means its absence was never an error.
    "default_200_percent/chromium/linux/product_logo_16.png": 32,
    "default_200_percent/chromium/linux/product_logo_32.png": 64,
}


# --------------------------------------------------------------- rendering


def _square_png(size: int) -> bytes:
    """Render the mark centred on a transparent `size`x`size` RGBA canvas."""
    import cairosvg
    from PIL import Image

    box = max(1, int(round(size * (1 - 2 * MARGIN))))
    # The viewBox is square, so fitting by height also fits by width; cairosvg
    # preserves the aspect from the viewBox.
    raw = cairosvg.svg2png(url=str(SOURCE_SVG), output_height=box)
    mark = Image.open(io.BytesIO(raw)).convert("RGBA")

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Paste WITHOUT a mask. Passing `mark` as its own mask looks like the
    # obvious way to preserve transparency, but it composites onto the canvas's
    # transparent BLACK: an edge pixel of colour C and alpha a comes out as
    # (C*a, a*a) -- RGB silently premultiplied and alpha squared. That is a dark
    # fringe plus a too-transparent edge, and at 16px, where nearly every pixel
    # is an edge pixel, it visibly muddies the icon. Maskless paste copies all
    # four bands verbatim, which is exactly right into an empty region.
    canvas.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _square_image(size: int):
    from PIL import Image

    return Image.open(io.BytesIO(_square_png(size))).convert("RGBA")


def _square_svg(extent: int) -> bytes:
    """Wrap the mark in a square viewBox, centred with the same margin.

    Consumers of product_logo.svg (and of the hicolor scalable icon) assume a
    square canvas with the same margin the PNGs have.
    """
    import re

    source = SOURCE_SVG.read_text()
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', source)
    if not match:
        raise SystemExit(f"ERROR: no parsable viewBox in {SOURCE_SVG}")
    src_w, src_h = float(match.group(1)), float(match.group(2))

    if FLAT_FILL.lower() not in source.lower():
        raise SystemExit(
            f"ERROR: {SOURCE_SVG.name} is not filled with {FLAT_FILL}. "
            "Set FLAT_FILL in generate-branding.py to the logo's fill."
        )

    body = re.search(r"<path\b.*?/>", source, re.S)
    if not body:
        raise SystemExit(f"ERROR: no <path> in {SOURCE_SVG}")

    box = extent * (1 - 2 * MARGIN)
    scale = min(box / src_w, box / src_h)
    dx = (extent - src_w * scale) / 2
    dy = (extent - src_h * scale) / 2

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{extent}" height="{extent}" viewBox="0 0 {extent} {extent}">\n'
        f'  <g transform="translate({dx:.4f} {dy:.4f}) scale({scale:.6f})">\n'
        f"    {body.group(0)}\n"
        "  </g>\n"
        "</svg>\n"
    ).encode()


def _xpm(size: int) -> bytes:
    """Two-colour XPM: transparent plus the flat fill, alpha thresholded at 50%.

    XPM is an indexed format with a single fully-transparent entry and no
    partial alpha, so a hard edge is the only faithful option -- there is no
    background here to pre-blend the anti-aliased pixels against.
    """
    import numpy as np

    alpha = np.asarray(_square_image(size))[..., 3]
    lines = [
        "/* XPM */",
        f"static char * {XPM_SYMBOL}[] = {{",
        f'"{size} {size} 2 1",',
        '" \tc None",',
        f'".\tc {FLAT_FILL}",',
    ]
    rows = ["".join("." if px >= 128 else " " for px in row) for row in alpha]
    for i, row in enumerate(rows):
        end = "};" if i == len(rows) - 1 else ","
        lines.append(f'"{row}"{end}')
    return ("\n".join(lines) + "\n").encode()


def _ico() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    _square_image(256).save(
        buf, format="ICO", sizes=[(s, s) for s in ICO_SIZES]
    )
    return buf.getvalue()


def _icns() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    # Pillow's ICNS writer derives the whole ladder from a 1024px master.
    _square_image(1024).save(buf, format="ICNS")
    return buf.getvalue()


def build() -> dict:
    """Return {destination path: desired bytes} for every generated file."""
    desired: dict[Path, bytes] = {}

    for rel, size in BRANDING_PNGS.items():
        desired[BRANDING_DIR / rel] = _square_png(size)

    # Raw-SVG resources: IDR_PRODUCT_LOGO_SVG and IDR_PRODUCT_LOGO_ANIMATION_SVG.
    # The animation one is replaced by a static mark on purpose -- it is the
    # Chromium ball's spin, it has no Camouwolf equivalent, and a still frame is a
    # correct SVG where a leftover ball would be a stray Chromium tell.
    # Extents match the upstream files they stand in for.
    desired[BRANDING_DIR / "chromium/product_logo.svg"] = _square_svg(256)
    desired[BRANDING_DIR / "chromium/product_logo_animation.svg"] = _square_svg(160)

    desired[BRANDING_DIR / "chromium/linux/product_logo_32.xpm"] = _xpm(32)
    desired[BRANDING_DIR / "chromium/win/chromium.ico"] = _ico()
    desired[BRANDING_DIR / "chromium/mac/app.icns"] = _icns()

    # The XDG hicolor set. The scalable SVG is what modern GNOME actually picks
    # up; the PNGs are the fallback for DEs without librsvg.
    desired[DESKTOP_ICON_DIR / "camouwolf.svg"] = _square_svg(256)
    for size in DESKTOP_ICON_SIZES:
        desired[DESKTOP_ICON_DIR / f"camouwolf_{size}.png"] = _square_png(size)

    return desired


# ----------------------------------------------------------------- checking

# Max per-channel difference tolerated between a committed raster and a fresh
# render of it. Encoder and rasteriser versions move edge pixels by a unit or
# two; a logo that actually changed moves them by far more than that.
PIXEL_TOLERANCE = 4


def _same_raster(a: bytes, b: bytes, fmt: str | None = None) -> bool:
    import numpy as np
    from PIL import Image

    try:
        ia = Image.open(io.BytesIO(a))
        ib = Image.open(io.BytesIO(b))
        if fmt in ("ICO", "ICNS"):
            # Compare the size ladder, then the largest frame's pixels.
            if sorted(getattr(ia, "info", {}).get("sizes", []) or [ia.size]) != sorted(
                getattr(ib, "info", {}).get("sizes", []) or [ib.size]
            ):
                return False
        ia = ia.convert("RGBA")
        ib = ib.convert("RGBA")
        if ia.size != ib.size:
            return False
        delta = np.abs(
            np.asarray(ia).astype(int) - np.asarray(ib).astype(int)
        )
        return int(delta.max()) <= PIXEL_TOLERANCE
    except Exception:
        return False


def _matches(path: Path, want: bytes) -> bool:
    try:
        have = path.read_bytes()
    except FileNotFoundError:
        return False
    suffix = path.suffix.lower()
    if suffix in (".svg", ".xpm"):
        return have == want
    if suffix == ".ico":
        return _same_raster(have, want, "ICO")
    if suffix == ".icns":
        return _same_raster(have, want, "ICNS")
    return _same_raster(have, want)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify every icon matches the canonical SVG; exit non-zero if not",
    )
    args = parser.parse_args()

    if not SOURCE_SVG.is_file():
        print(f"ERROR: canonical vector not found: {SOURCE_SVG}", file=sys.stderr)
        return 2

    try:
        import cairosvg  # noqa: F401
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        print(
            f"ERROR: missing dependency ({exc.name}). "
            "Install with: pip install cairosvg pillow numpy",
            file=sys.stderr,
        )
        return 2

    desired = build()

    if args.check:
        stale = [p for p, want in desired.items() if not _matches(p, want)]
        if stale:
            print("Branding icons are OUT OF DATE vs the canonical logo:")
            for path in stale:
                print(f"  - {path.relative_to(BROWSER_DIR)}")
            print("Run: python3 browser/scripts/generate-branding.py")
            return 1
        print(f"All {len(desired)} branding icons are up to date.")
        return 0

    for path, want in desired.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(want)
        print(f"wrote {path.relative_to(BROWSER_DIR)} ({len(want)} bytes)")
    print(f"Done. Regenerated {len(desired)} files from {SOURCE_SVG.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
