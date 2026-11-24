// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.
//
// Seeded, deterministic sub-pixel perturbation for the canvas readback paths
// (canvas 2D getImageData, canvas toDataURL/toBlob), the OfflineAudioContext /
// AnalyserNode sample paths, and TextMetrics quantization.
//
// ============================================================================
// READ THIS BEFORE ENABLING ANYTHING HERE.
//
// Every switch in this file is OFF by default and must stay that way. This is
// not a conservative default — it is the measured-correct posture, and turning
// one on makes Camouwolf MORE identifiable, not less.
//
// Camoufox (the Firefox counterpart) reached the same conclusion the expensive way
// and deleted its audio farbling patch outright, then pref-gated its WebGL
// readback randomization off in beta.31 after measuring the un-noised output
// against real Windows Firefox on the same RTX 5080: 10 of 12 WebGL scenes were
// byte-identical, `fp-classic` — the scene commodity fingerprinters actually
// hash — among them. There was nothing left for the noise to hide.
//
// CAMOUWOLF'S CASE IS STRICTLY STRONGER THAN CAMOUFOX'S. Camoufox had to KEEP one
// randomizer: stock Firefox 152 perturbs the encode path (toDataURL/toBlob)
// itself under baseline EfficientCanvasRandomization, so removing that would
// have been a tell in the other direction. Stock Chrome randomizes NOTHING.
// There is no native noise to imitate, so every byte of perturbation this file
// applies is un-Chrome-like on its face, with no compensating cover.
//
// Two independent detections, neither of which needs a reference machine:
//
//   1. CROSS-SURFACE. A detector that hashes both getImageData() and
//      toDataURL() of one canvas, or a WebGL readback against a 2D re-draw,
//      catches a browser that perturbs the paths independently.
//   2. CROSS-ORIGIN. If the key is per-first-party, the same scene drawn by the
//      same GPU in one session hashes differently depending on who asked. No
//      hardware does that, and a third-party iframe reads it straight off.
//      the build-tester noise checks (ci/run_build_tester.py) are the gate for exactly this.
//
// And measurement says the noise does not even buy what it was meant to buy:
//   * docs/WINDOWS-DIVERGENCE.md §2.1 — the Linux canvas first-read instability
//     passes straight THROUGH farbling, because both reads get the same seeded
//     delta. The tell it was supposed to mask survives it.
//   * docs/WINDOWS-DIVERGENCE.md §2.8 — the audio residue is one 1000-sample
//     chunk of ULP-scale platform-math drift, not a per-device signal. That is
//     an 0018-shaped problem (host libm parity), and per-identity noise makes
//     it strictly worse by replacing a stable value with a drifting one that
//     matches no real reference.
//
// So these switches exist to MEASURE the old posture without a rebuild, the way
// Camoufox kept `its WebGL readback noise pref`. They are not a product feature.
//
// WHAT IS NOT FARBLED (this header used to claim otherwise):
//   WebGL `readPixels` is NOT perturbed. Patch 0009 touches
//   base_rendering_context_2d.cc and image_data_buffer.cc only;
//   WebGLRenderingContextBase::readPixels is untouched. The cross-surface
//   argument above is precisely the gap that would create if canvas farbling
//   were ever switched on — a detector comparing a WebGL readback against a 2D
//   re-draw of the same scene would see one perturbed and one not. That is a
//   further reason the default must remain off, and it is why this note is here
//   rather than in a commit message.
// ============================================================================
//
// WHY ONE HELPER AND NOT THREE:
//   Every surface here derives from the same (seed, x, y, channel, value)
//   function, so a given pixel gets the same delta no matter which API read it.
//   That is what keeps the cross-surface check in (1) above consistent for the
//   surfaces that ARE covered.
//
// WHY DETERMINISTIC AND NOT RANDOM:
//   Per-read randomness is the *loudest* possible signal: a page that calls
//   getImageData() twice on an unchanged canvas and gets two different hashes
//   has proved it is being farbled, which is worse than reporting a stable
//   uncommon hash. Real hardware is stable across reads. So the delta is a pure
//   function of position and value — stable within a session, different between
//   identities.
//
// MAGNITUDE:
//   +/-1 on one of R,G,B, alpha untouched. Enough to move a SHA-256 of the
//   buffer; small enough that it survives PNG encoding, is invisible on screen,
//   and does not perturb the pixel-accumulating checks (mean/variance over a
//   region) that some detectors use as an anti-farble tripwire.

#ifndef BASE_CAMOUWOLF_FARBLE_H_
#define BASE_CAMOUWOLF_FARBLE_H_

#include <cstddef>
#include <cstdint>

#include "base/containers/span.h"
#include "base/camouwolf_config.h"

namespace base {

// The per-surface opt-in flags. Absent (the launcher's default) means off.
//
// Each surface gets its OWN key on purpose. `canvas:seed` used to arm all three
// at once, so a single config value flipped canvas readback, audio samples and
// TextMetrics quantization together — three unrelated tells from one number,
// which is how a "harmless" seed becomes a fleet-wide marker. A seed is now a
// seed: it supplies the delta, it does not decide whether a delta is applied.
//
// Each key is spelled out as a LITERAL at its own GetBool call rather than
// passed into a shared helper. scripts/check-config-keys.py joins the launcher
// and the browser by grepping for exactly this shape, so a key reached through
// a `const char*` parameter is invisible to it — the reader could be deleted
// and the gate would report the config clean. Three near-identical functions is
// the price of staying greppable, and it is worth paying.
inline bool CamouwolfCanvasRandomizeReadback() {
  const CamouwolfConfig& cfg = CamouwolfConfig::Get();
  return cfg.IsActive() &&
         cfg.GetBool("canvas:randomizeReadback").value_or(false);
}

inline bool CamouwolfAudioRandomizeSamples() {
  const CamouwolfConfig& cfg = CamouwolfConfig::Get();
  return cfg.IsActive() &&
         cfg.GetBool("AudioContext:randomizeSamples").value_or(false);
}

inline bool CamouwolfQuantizeTextMetricsEnabled() {
  const CamouwolfConfig& cfg = CamouwolfConfig::Get();
  return cfg.IsActive() &&
         cfg.GetBool("canvas:quantizeTextMetrics").value_or(false);
}

// The per-launch visual seed. This is the delta SOURCE only — it arms nothing.
inline uint32_t CamouwolfVisualSeed() {
  static const uint32_t seed = [] {
    const CamouwolfConfig& cfg = CamouwolfConfig::Get();
    if (!cfg.IsActive()) {
      return 0u;
    }
    return cfg.GetUint32("canvas:seed").value_or(0u);
  }();
  return seed;
}

// Canvas 2D readback perturbation (patch 0009). Off unless explicitly armed.
inline uint32_t CamouwolfCanvasSeed() {
  static const uint32_t seed = [] {
    return CamouwolfCanvasRandomizeReadback() ? CamouwolfVisualSeed() : 0u;
  }();
  return seed;
}

// OfflineAudioContext / AnalyserNode sample perturbation (patch 0011). Off
// unless explicitly armed. Separate from the canvas key because the two are
// separate decisions with separate evidence — see WINDOWS-DIVERGENCE §2.8.
inline uint32_t CamouwolfAudioSeed() {
  static const uint32_t seed = [] {
    return CamouwolfAudioRandomizeSamples() ? CamouwolfVisualSeed() : 0u;
  }();
  return seed;
}

// The signed delta for one channel byte. `index` is the byte's offset within the
// RGBA buffer, so the (x, y) position is implied by index >> 2 and the channel
// by index & 3.
inline int CamouwolfPixelDelta(uint32_t seed, size_t index, uint8_t value) {
  uint32_t z = seed;
  z ^= static_cast<uint32_t>(index >> 2) * 374761393u;   // pixel
  z ^= static_cast<uint32_t>(index & 3u) * 0x9e3779b9u;  // channel
  z ^= static_cast<uint32_t>(value) * 2654435761u;       // value
  // A round of murmur3 finalization — cheap, and good enough that neighbouring
  // pixels do not share a delta (a visible pattern would itself be detectable).
  z ^= z >> 16;
  z *= 0x85ebca6bu;
  z ^= z >> 13;
  z *= 0xc2b2ae35u;
  z ^= z >> 16;
  // 1 bit of sign, and 1-in-4 pixels left completely untouched so the delta
  // field is sparse rather than a uniform dither.
  if ((z & 0x3u) == 0u) {
    return 0;
  }
  return (z & 0x4u) ? 1 : -1;
}

// Perturb an RGBA byte buffer in place. Alpha (every 4th byte) is skipped: a
// canvas whose alpha channel drifts breaks compositing tests and is trivially
// separable from real hardware noise.
inline void CamouwolfFarbleRGBA(span<uint8_t> pixels) {
  const uint32_t seed = CamouwolfCanvasSeed();
  if (seed == 0u) {
    return;
  }
  for (size_t i = 0; i < pixels.size(); ++i) {
    if ((i & 3u) == 3u) {
      continue;  // alpha
    }
    const int delta = CamouwolfPixelDelta(seed, i, pixels[i]);
    if (delta == 0) {
      continue;
    }
    const int next = static_cast<int>(pixels[i]) + delta;
    pixels[i] = static_cast<uint8_t>(next < 0 ? 0 : (next > 255 ? 255 : next));
  }
}

// Quantize a text-measurement double so TextMetrics cannot be used as a
// high-precision font/rasterizer probe.
//
// Off by default, and armed by its own key. Real Chrome returns a
// float-precision width whose low bits encode the exact hinting and font
// version, so quantizing is itself a divergence from stock Chrome — it trades a
// high-entropy value for an uncommon one. Rounding to 1/64 px (the
// FreeType/DirectWrite 26.6 fixed-point grid that both rasterizers actually use
// internally) at least keeps every returned value one the claimed platform
// COULD have produced, which is what naive rounding to whole pixels gets wrong.
inline double CamouwolfQuantizeTextMetric(double value) {
  static const bool enabled = CamouwolfQuantizeTextMetricsEnabled();
  if (!enabled || CamouwolfVisualSeed() == 0u) {
    return value;
  }
  constexpr double kGrid = 64.0;  // 26.6 fixed point
  return static_cast<double>(static_cast<int64_t>(value * kGrid + 0.5)) / kGrid;
}

}  // namespace base

#endif  // BASE_CAMOUWOLF_FARBLE_H_
