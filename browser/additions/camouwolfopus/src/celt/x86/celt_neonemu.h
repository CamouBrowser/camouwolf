/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/* AArch64 Opus kernels emulated on x86 (see celt_neonemu.c). */

#ifndef CELT_NEONEMU_H
#define CELT_NEONEMU_H

#include "arch.h"
#include "cpu_support.h"

#if defined(OPUS_ARCH_X86_NEONEMU) && !defined(FIXED_POINT)
#define OPUS_X86_NEONEMU (1)
#define OPUS_X86_IS_NEONEMU(arch) ((arch) == OPUS_ARCH_X86_NEONEMU)

opus_val32 celt_inner_prod_neonemu(const opus_val16 *x, const opus_val16 *y,
      int N);
void dual_inner_prod_neonemu(const opus_val16 *x, const opus_val16 *y01,
      const opus_val16 *y02, int N, opus_val32 *xy1, opus_val32 *xy2);
void celt_pitch_xcorr_neonemu(const opus_val16 *_x, const opus_val16 *_y,
      opus_val32 *xcorr, int len, int max_pitch, int arch);
#endif

#endif
