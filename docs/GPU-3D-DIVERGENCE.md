# Chrome's GPU/3D output: Windows vs Linux on one RTX 5080

Measured 2026-08-20. Chrome for Testing **152.0.7977.54** on both sides — the
same build, pushed to the Windows VM over ssh — rendering on the **same physical
RTX 5080**, passed through to a Windows 11 guest for the Windows leg and bound to
the nvidia driver for the Linux leg.

Captures (pixel-free digests): `tests/compare/captures/gpu-5080/`.
Full captures with pixels, the probes, and the diff tooling live in
`../Holo/tests/capture/gpu-re/` (see its `WINDOWS-3D-REFERENCE.md`).

## Why this supersedes the GPU parts of WINDOWS-DIVERGENCE.md

That report compares Windows Chrome on an **Intel UHD laptop** against Linux
Chrome on this **RTX 5080**, and says so plainly: *"Leg B's GPU is an RTX 5080 and
leg A's is an Intel UHD, so GPU-specific differences are confounded."* Every
rendering difference it records could have been the GPU rather than the OS.

Here the GPU is held fixed, so a difference is the OS or the browser and nothing
else. **Firefox was captured on the same four legs as a control** — that is what
separates "Chromium does this" from "the driver does this", and it changes the
reading of several results below.

Both sides are exactly reproducible: Windows 44/44 corpus scenes identical across
three warm runs and a guest reboot; Linux 44/44 across five runs, and 44/44 again
across a GPU rebind and a host reboot.

## 1. Chrome diverges more than twice as much as Firefox

Same corpus, same GPU, same two operating systems:

| engine | scenes identical | scenes differing |
| --- | --- | --- |
| Firefox 152.0.3 | 34 | **8** |
| Chrome 152.0.7977.54 | 24 | **18** |

The extra ten are almost entirely **2D canvas**. Firefox differs on exactly one
canvas scene (`canvas2d-text`, 9 px); Chrome differs on **all ten**:

| scene | differing pixels | max Δ | mean Δ |
| --- | --- | --- | --- |
| `canvas2d-text` | 2874/16384 (17.5%) | 239 | 105.2 |
| `canvas2d-composite` | 1450 (8.9%) | 19 | 2.5 |
| `canvas2d-lines` | 849 (5.2%) | 90 | 26.8 |
| `canvas2d-shapes` | 472 (2.9%) | 55 | 13.0 |
| `canvas2d-filter` | 28 (0.2%) | 14 | 2.5 |
| plus `canvas2d-shadow`, `-pattern`, `-drawimage-scale`, `-conic`, `-clip-alpha` | | | |

This is the single biggest Chromium-side finding. Holo reached byte-identical 2D
canvas by substituting DirectWrite for text and forcing the accelerated canvas
path; Camouwolf has done neither, so its canvas is OS-divergent across shapes, lines,
compositing and filters — not just text. `canvas2d-text` at mean Δ 105 is a
different glyph rasteriser (DirectWrite vs FreeType), but shapes/lines/composite
diverging points at the Skia backend as well (D3D vs GL), which text substitution
alone will not fix.

## 2. `tex-cube` diverges in Chrome and NOT in Firefox

`tex-cube` (cube-map face selection and seam filtering) is byte-identical between
Windows and Linux Firefox, and differs in Chrome by 2554/16384 px (15.6%,
max Δ 146). Same GPU, same driver, same two OSes — so this is **Chromium's own**
cube-map setup, not a driver difference. Worth tracing: it is a WebGL scene, so
it is reachable by any page with a canvas.

## 3. Three divergences are driver-level and NOT Chromium's fault

Exact-bit probes, all four legs:

| probe | win/ff | win/cr | lin/ff | lin/cr | verdict |
| --- | --- | --- | --- | --- | --- |
| `deriv-exact` | ee24e46d | ee24e46d | 3e33c4ff | 3e33c4ff | **OS-split**, engine-independent |
| `pointsize-sweep` | 9ab0d187 | 9ab0d187 | 99d5f2ed | 99d5f2ed | **OS-split**, engine-independent |
| `intfun-exact` | 9fd2dca3 | 9fd2dca3 | 4e750322 | 4e750322 | **OS-split**, engine-independent |
| `mipgen-exact` | efb69dc5 | efb69dc5 | efb69dc5 | efb69dc5 | identical everywhere |
| `texfilter-exact` | 5e509dc5 | 5e509dc5 | 5e509dc5 | 5e509dc5 | identical everywhere |
| `lod-exact` | 3c6a1edc | 3c6a1edc | 3c6a1edc | 3c6a1edc | identical everywhere |
| `sign-forms` | 2dc9fd45 | 2dc9fd45 | 2dc9fd45 | 2dc9fd45 | identical everywhere |
| `svg-filter` | 8bb23c3b | 84281f14 | 8bb23c3b | 84281f14 | **engine-split**, OS-independent |

Chrome and Firefox agree byte-for-byte within each OS on all three OS-split
probes, so they belong to **ANGLE/D3D11-vs-GL**, not to either engine:

- **Derivatives.** Windows computes `dFdx`/`dFdy` once per 2×2 quad from the
  quad's bottom-left fragment and broadcasts it (coarse); Linux differences along
  each fragment's own row/column (fine). Both rules verified exactly, 3969/3969
  fragments. Structurally visible: 1024/1024 quads carry one shared value on
  Windows, 0/1024 on Linux — a page detects this with a three-line shader.
- **Point sprites.** D3D11 has no point primitive, so ANGLE emulates them in
  shader code using the exact fp32 `gl_PointSize`; Linux uses the hardware unit,
  which **quantizes size to 1/32 px**.
- **Builtins.** 38/40 identical. `%` with a negative operand differs (undefined in
  ESSL, hence a perfectly stable discriminator), plus one packing builtin.

Holo has already proven the derivative one is fixable: emulating the coarse rule
arithmetically on Linux reproduces the Windows scene **byte-identically**. The
same fix applies to Chromium — it is an ANGLE-level rewrite, and ANGLE is shared.

**`svg-filter` is an engine tell, not an OS tell.** Each engine agrees with itself
across both OSes. Chrome's SVG filter output differs from Firefox's everywhere,
so it is not part of the OS-spoofing problem.

## 4. WebGPU is a Chrome-only OS tell

| | `navigator.gpu` | adapter | render/compute |
| --- | --- | --- | --- |
| Windows Chrome | present | **yes** | `e024b9c8` / `2728f455` |
| Linux Chrome | present | **none** | skipped |
| Firefox (both) | absent | — | — |

`navigator.gpu` exists in Chrome on both operating systems, but only Windows has
an adapter — so a page gets real WebGPU results on Windows and "no adapter" on
Linux, from the same browser version on the same card. Combined with
`getPreferredCanvasFormat()` (already flagged in WINDOWS-DIVERGENCE.md §2.3) this
is a zero-cost, zero-permission OS discriminator that Firefox does not have.
Camouwolf owns this one entirely.

## 5. The renderer string is unbucketed in Chrome

Chrome reports the real adapter on both sides:

```
windows  ANGLE (NVIDIA, NVIDIA GeForce RTX 5080 (0x00002C02) Direct3D11 vs_5_0 ps_5_0, D3D11)
linux    ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 5080/PCIe/SSE2, OpenGL 4.5.0)
```

The backend suffix (`Direct3D11 …, D3D11` vs `OpenGL 4.5.0`) names the OS
directly, which WINDOWS-DIVERGENCE.md §2.2 already covers. What is new is the
confirmation that the **rendering behind those strings genuinely differs** in the
three ways above, so spoofing the string alone leaves a page able to check it
against measured behaviour.

## What to fix, in order

1. **2D canvas** — ten scenes, the largest surface, and entirely Chromium's.
2. **WebGPU adapter availability** — trivially readable, Chrome-only.
3. **Derivatives / point sprites / builtins** — ANGLE-level, fix shared with Holo.
4. **`tex-cube`** — Chromium-specific, cause not yet traced.
5. **`caps-full`** — `MAX_VERTEX_UNIFORM_VECTORS` and friends read one short on a
   D3D11 backend; `COMPRESSED_TEXTURE_FORMATS` differs too.

## Reproducing

The rig is in Holo. From `../Holo`:

```bash
# Windows leg (card on vfio from boot; capture BEFORE handing it to Linux)
WINVM_GPU=1 bash tests/capture/dwrite-re/winvm.sh run &
bash tests/capture/gpu-re/capture-all.sh windows win5080

# Linux leg
sudo /usr/local/sbin/holo-gpu-bind nvidia
HOLO_GPU=01:00.0 bash tests/capture/gpu-re/capture-all.sh linux lin5080
```

Two traps, both of which produced confident wrong numbers before being caught:
`HOLO_GPU=128` selects the **AMD XTX**, not the 5080 (`renderD129` / `01:00.0`);
and Chrome launched from a Windows Scheduled Task **ignores a command-line URL**,
so it must be navigated through the DevTools `/json/new` endpoint.
