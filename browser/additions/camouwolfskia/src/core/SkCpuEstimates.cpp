// Copyright 2026 The Camouwolf Authors.
// Use of this source code is governed by a BSD-style license.

// The CPU whose arithmetic Skia reproduces, as the embedder set it
// (SkCpuEstimates.h). Off x86 there is nothing to reproduce.

#include "src/opts/SkCpuEstimates.h"

#include "include/private/SkFeatures.h"

#if defined(SK_CPU_X86)
#include "src/opts/SkCpuEstimateTables.h"
#endif

namespace {
// Written once, before SkGraphics::Init() starts any thread that reads them.
bool gPinned = false;
int gEmulated = SK_CPU_VENDOR_HOST;
}  // namespace

void sk_cpu_set_identity(bool pinned, int emulated) {
    gPinned = pinned;
    gEmulated = emulated;
}

const uint32_t* sk_cpu_estimates() {
#if defined(SK_CPU_X86)
    if (gEmulated == SK_CPU_VENDOR_INTEL || gEmulated == SK_CPU_VENDOR_AMD) {
        return kSkCpuEstimateTables[gEmulated - 1];
    }
#endif
    return nullptr;
}

bool sk_cpu_emulates_arm64() {
#if defined(SK_CPU_X86)
    return gEmulated == SK_CPU_IDENTITY_ARM64;
#else
    return false;
#endif
}

bool sk_cpu_pinned() { return gPinned; }
