/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/*
 * The CPU whose arithmetic libopus reproduces.
 *
 * The encoder's PVQ search (celt/x86/vq_sse2.c) uses RCPPS and RSQRTPS, whose
 * bits differ between Intel and AMD, so an encoded stream shows which vendor
 * encoded it. Like Skia, it can compute the other vendor's bits from a
 * recorded table; it is handed Skia's own (sk_cpu_estimates()), so the two
 * libraries cannot disagree about the vendor.
 *
 * When the identity is an AArch64 machine, x86 encoders and decoders instead
 * run the kernels Chromium's arm64 build runs (celt/x86/celt_neonemu.c).
 *
 * libopus cannot read the Camouwolf config, so the embedder calls the setter
 * (content/common/skia_utils.cc).
 */

#ifndef OPUS_CPU_IDENTITY_H
#define OPUS_CPU_IDENTITY_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Set once per process, before any encoder or decoder is created and before
 * a thread that creates one starts: the recorded table to reproduce or NULL
 * for this CPU's own instructions, and nonzero to run the AArch64 kernels. */
void opus_cpu_set_identity(const uint32_t *estimates, int emulate_arm64);

const uint32_t *opus_cpu_estimates(void);
int opus_cpu_emulates_arm64(void);

#ifdef __cplusplus
}
#endif

#endif
