#!/usr/bin/env python3
"""Trace browser/assets/CamouwolfLogo.png into the canonical Camouwolf vector.

The source art is a 1254x1254 RGB raster with no alpha: a periwinkle reader
silhouette painted onto white, with the paint varying by a few units across the
mark (it came out of an image generator, not a vector tool). Two things make it
unusable as a browser icon as-is:

  * **No transparency.** A white-backed square is exactly what a dock icon must
    not be -- it renders as a white tile on every dark theme.
  * **No single colour.** ~4570 distinct RGB values, so any two crops of the
    mark are subtly different shades and nothing downstream can be recoloured.

So this script rebuilds the mark as geometry:

  1. Threshold at 50% ink coverage -- the same place the eye reads the edge --
     to get a clean silhouette mask.
  2. Trace that mask with potrace (the pure-Python `potracer` port) into cubic
     Beziers.
  3. Emit ONE path, ONE `fill`, `fill-rule="evenodd"` so the enclosed counters
     (the face, the book's pages, the gaps between hair strands) stay open.

The fill is FLAT_FILL below, which is both the mean and the median of the
source's interior pixels -- so the flattened mark is the colour the original
already read as, not a colour picked by eye.

Output: browser/assets/CamouwolfLogo.svg, viewBox cropped tight to the mark. Tight
is deliberate: every consumer needs a different amount of padding around it, and
you can add margin to a tight mark but you cannot recover it from a padded one.
generate-branding.py is the only thing that should be adding that padding.

Usage:
    python3 browser/scripts/vectorize-logo.py            # write the SVG
    python3 browser/scripts/vectorize-logo.py --check    # verify, don't write

Requires: pillow, numpy, potracer  (pip install pillow numpy potracer)

This is a re-run-on-demand tool, not a build step: the traced SVG is committed,
and the PNG only changes when the logo itself does.
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BROWSER_DIR = SCRIPT_DIR.parent
SOURCE_PNG = BROWSER_DIR / "assets" / "CamouwolfLogo.png"
OUTPUT_SVG = BROWSER_DIR / "assets" / "CamouwolfLogo.svg"

# The one colour the whole mark is flattened to. Mean and median of the source's
# eroded interior agree on this exactly; see the module docstring.
FLAT_FILL = "#AFBBD7"

# Luminance of the source's paper and of its ink core. The mask is the 50%
# crossing between them, which is where an anti-aliased edge visually sits.
PAPER_LUMA = 254.0
INK_LUMA = 193.0

# Suppress traced regions smaller than this many px^2. At 1254px the smallest
# real feature (the notch under the lip) is ~370 px^2, and the largest
# generator speck is ~11 px^2 -- an order of magnitude of daylight between
# them, so this threshold is not a close call.
TURDSIZE = 64

# potrace curve-fitting knobs, both upstream defaults. alphamax is the
# corner-vs-curve threshold: the mark's hair spikes need to stay sharp, and 1.0
# keeps them. opttolerance is the Bezier-joining error budget.
ALPHAMAX = 1.0
OPTTOLERANCE = 0.2

# Coordinate precision in the emitted path data. The mark is ~1000 units across,
# so 2dp is ~0.01px of error at native size -- invisible, and it keeps the file
# a third of the size of full float repr.
PRECISION = 2


def build_mask():
    """Return a bool array: True where the source has >=50% ink coverage."""
    import numpy as np
    from PIL import Image

    luma = (
        np.asarray(Image.open(SOURCE_PNG).convert("RGB"))
        .astype(np.float64)
        .mean(axis=2)
    )
    coverage = np.clip((PAPER_LUMA - luma) / (PAPER_LUMA - INK_LUMA), 0.0, 1.0)
    return coverage >= 0.5


def trace(mask):
    """Trace `mask` and return potrace's list of closed curves."""
    import potrace

    # potracer's Bitmap inverts whatever it is handed, so pass the complement:
    # ~mask in, mask traced. Handing it `mask` directly traces the PAPER, which
    # comes back as a single rectangle covering the whole canvas -- a silent
    # wrong answer rather than an error, so it is worth being explicit here.
    bitmap = potrace.Bitmap(~mask)
    return bitmap.trace(
        turdsize=TURDSIZE,
        alphamax=ALPHAMAX,
        opticurve=True,
        opttolerance=OPTTOLERANCE,
    )


def render_svg(curves, mask):
    """Serialise `curves` as a one-path SVG cropped tight to the mark."""
    import numpy as np

    rows, cols = np.where(mask)
    min_x, max_x = float(cols.min()), float(cols.max() + 1)
    min_y, max_y = float(rows.min()), float(rows.max() + 1)
    width, height = max_x - min_x, max_y - min_y

    def n(value):
        # Trim the trailing zeros 2dp leaves behind ("12.30" -> "12.3").
        return f"{value:.{PRECISION}f}".rstrip("0").rstrip(".") or "0"

    def pt(p):
        return f"{n(p.x - min_x)} {n(p.y - min_y)}"

    parts = []
    for curve in curves:
        parts.append(f"M{pt(curve.start_point)}")
        for segment in curve.segments:
            if segment.is_corner:
                parts.append(f"L{pt(segment.c)}")
                parts.append(f"L{pt(segment.end_point)}")
            else:
                parts.append(
                    f"C{pt(segment.c1)} {pt(segment.c2)} {pt(segment.end_point)}"
                )
        parts.append("Z")

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{n(width)}" height="{n(height)}" '
        f'viewBox="0 0 {n(width)} {n(height)}">\n'
        f'  <path fill="{FLAT_FILL}" fill-rule="evenodd" d="{"".join(parts)}"/>\n'
        "</svg>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the SVG matches the PNG; exit non-zero if not (no writes)",
    )
    args = parser.parse_args()

    if not SOURCE_PNG.is_file():
        print(f"ERROR: source art not found: {SOURCE_PNG}", file=sys.stderr)
        return 2

    try:
        import numpy  # noqa: F401
        import potrace  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        print(
            f"ERROR: missing dependency ({exc.name}). "
            "Install with: pip install pillow numpy potracer",
            file=sys.stderr,
        )
        return 2

    mask = build_mask()
    curves = trace(mask)
    svg = render_svg(curves, mask)

    if args.check:
        current = OUTPUT_SVG.read_text() if OUTPUT_SVG.is_file() else ""
        if current != svg:
            print(f"{OUTPUT_SVG.relative_to(BROWSER_DIR)} is out of date vs "
                  f"{SOURCE_PNG.name}.")
            print("Run: python3 browser/scripts/vectorize-logo.py")
            return 1
        print(f"{OUTPUT_SVG.name} is up to date with {SOURCE_PNG.name}.")
        return 0

    OUTPUT_SVG.write_text(svg)
    segments = sum(len(c.segments) for c in curves)
    print(
        f"wrote {OUTPUT_SVG.relative_to(BROWSER_DIR)} "
        f"({len(svg)} bytes, {len(curves)} contours, {segments} segments, "
        f"fill {FLAT_FILL})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
