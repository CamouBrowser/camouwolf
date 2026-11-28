/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/* AArch64 Opus kernels for x86, selected as arch OPUS_ARCH_X86_NEONEMU
 * when the identity is an arm64 CPU (opus_cpu_identity.h).
 *
 * Each function reproduces, value for value, what Chromium's arm64 build
 * computes:
 *   celt_inner_prod / dual_inner_prod  <- celt/arm/pitch_neon_intr.c
 *   celt_pitch_xcorr                   <- celt/arm/celt_neon_intr.c
 * Every other float kernel is plain C on arm64 and runs as C here (the
 * dispatch in pitch_sse.h, vq_sse.h and the RTCD maps).
 * aarch64 NEON float lanes are plain IEEE single precision, so a lane is a
 * C float. vmlaq/vmlaq_lane become vfmaq (__ARM_FEATURE_FMA) and are fused:
 * fmaf() here. Everything else is unfused, as is the scalar C both builds
 * share (Chromium builds with -ffp-contract=off). The order of every add
 * follows the NEON lane layout, not the C reference. */

#ifdef HAVE_CONFIG_H
#include "config.h"
#endif

#include <math.h>

#include "x86/celt_neonemu.h"

#if defined(OPUS_X86_NEONEMU)

#pragma STDC FP_CONTRACT OFF

opus_val32 celt_inner_prod_neonemu(const opus_val16 *x, const opus_val16 *y,
      int N)
{
   int i, k;
   float acc[4] = {0, 0, 0, 0};
   float xy;
   for (i = 0; i < N - 7; i += 8) {
      for (k = 0; k < 4; k++) acc[k] = fmaf(x[i + k], y[i + k], acc[k]);
      for (k = 0; k < 4; k++) acc[k] = fmaf(x[i + 4 + k], y[i + 4 + k], acc[k]);
   }
   if (N - i >= 4) {
      for (k = 0; k < 4; k++) acc[k] = fmaf(x[i + k], y[i + k], acc[k]);
      i += 4;
   }
   /* vadd_f32(low, high), then vpadd_f32 */
   xy = (acc[0] + acc[2]) + (acc[1] + acc[3]);
   for (; i < N; i++) {
      xy = xy + x[i] * y[i];
   }
   return xy;
}

void dual_inner_prod_neonemu(const opus_val16 *x, const opus_val16 *y01,
      const opus_val16 *y02, int N, opus_val32 *xy1, opus_val32 *xy2)
{
   *xy1 = celt_inner_prod_neonemu(x, y01, N);
   *xy2 = celt_inner_prod_neonemu(x, y02, N);
}

/* xcorr_kernel_neon_float: lane k accumulates y[j + k] * x[j] with one fused
   multiply-add per j, in order j = 0 .. len - 1, from 0. */
static void xcorr_kernel_neonemu(const float *x, const float *y,
      float sum[4], int len)
{
   int j, k;
   float acc[4] = {0, 0, 0, 0};
   for (j = 0; j < len; j++) {
      for (k = 0; k < 4; k++) acc[k] = fmaf(y[j + k], x[j], acc[k]);
   }
   for (k = 0; k < 4; k++) sum[k] = acc[k];
}

void celt_pitch_xcorr_neonemu(const opus_val16 *_x, const opus_val16 *_y,
      opus_val32 *xcorr, int len, int max_pitch, int arch)
{
   int i;
   (void)arch;
   for (i = 0; i < (max_pitch - 3); i += 4) {
      xcorr_kernel_neonemu(_x, _y + i, xcorr + i, len);
   }
   for (; i < max_pitch; i++) {
      xcorr[i] = celt_inner_prod_neonemu(_x, _y + i, len);
   }
}

#endif
