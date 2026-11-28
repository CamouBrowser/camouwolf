/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/* The state opus_cpu_identity.h describes. Built on every CPU so the embedder
 * links the same way everywhere; only x86 reads it (the PVQ search and
 * opus_select_arch). */

#include "opus_cpu_identity.h"

/* Written once, before any thread that reads them starts. */
static const uint32_t *gEstimates;
static int gEmulateArm64;

void opus_cpu_set_identity(const uint32_t *estimates, int emulate_arm64)
{
   gEstimates = estimates;
   gEmulateArm64 = emulate_arm64 != 0;
}

const uint32_t *opus_cpu_estimates(void)
{
   return gEstimates;
}

int opus_cpu_emulates_arm64(void)
{
   return gEmulateArm64;
}
