# GPU/3D captures — one RTX 5080, two OSes, two engines

Pixel-free digests of the GPU render-parity captures described in
`../../../../docs/GPU-3D-DIVERGENCE.md`.

Every file here is a capture with its `b64` pixel blobs removed. The blobs are
~99% of the bytes and are only needed for pixel-level diffing, which happens in
Holo where the quantifier lives — keeping them here would have made this
directory 45x the size of every other capture in `tests/compare/captures/`. What
remains is everything that identifies: per-scene hashes, the full capability
surface, and the structured probe results (MSAA sample ladders, std140 block
layout, transform feedback, readback format/type pairs).

Each file records its origin in `_source`. The full captures with pixels are at
`../Holo/tests/capture/gpu-re/results/`, and the Windows ones are committed there
(that half is irreplaceable without another VM session; the Linux half regenerates
on demand).

| file | leg |
| --- | --- |
| `windows-chrome-{corpus,traits,surface}.json` | Chrome 152.0.7977.54, Windows 11 guest, 5080 passed through |
| `linux-chrome-{corpus,traits,surface}.json` | same Chrome build, Linux, same 5080 |
| `windows-firefox-{corpus,traits}.json` | Firefox 152.0.3 — the control leg |
| `linux-firefox-{corpus,traits}.json` | Firefox 152.0.3, Linux |

**The Firefox files are why this is worth having.** With one engine you cannot
tell a Chromium bug from a driver difference. With both, the three exact-bit
divergences resolve as driver-level (Chrome and Firefox agree within each OS)
while the ten 2D-canvas divergences resolve as Chromium's alone (Firefox differs
on one canvas scene, Chrome on all ten).
