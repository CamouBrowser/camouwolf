/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// Skia's AArch64 swizzlers on x86-64, for cpu:identity = ARM64.

#include "include/private/SkFeatures.h"

#if defined(SK_CPU_X86) && !defined(SK_ENABLE_OPTIMIZE_SIZE)

#include "include/core/SkTypes.h"
#include "include/private/SkFeatures.h"
#include "include/private/SkMalloc.h"
#include "include/core/SkSpan.h"
#include "include/private/SkTemplates.h"
#include "modules/skcms/skcms.h"
#include "src/core/SkMSAN.h"
#include "src/core/SkUtils.h"
#include "src/core/SkVx.h"
#include "src/core/Sk4px.h"
#include "src/core/SkBitmapProcState.h"
#include "src/core/SkBlitMask.h"
#include "src/core/SkBlitRow.h"
#include "src/core/SkColorData.h"
#include "src/core/SkOpts.h"
#include "src/core/SkRasterPipeline.h"
#include "src/core/SkRasterPipelineContextUtils.h"
#include "src/core/SkSwizzlePriv.h"
#include "src/shaders/SkPerlinNoiseShaderType.h"
#include "src/sksl/tracing/SkSLTraceHook.h"

#include "src/opts/SkOpts_NeonEmuTarget.h"

#include "src/opts/SkSwizzler_opts.inc"

namespace SkOpts {
    void Init_Swizzler_neonemu() {
        RGBA_to_BGRA          = neonemu::RGBA_to_BGRA;
        RGBA_to_rgbA          = neonemu::RGBA_to_rgbA;
        RGBA_to_bgrA          = neonemu::RGBA_to_bgrA;
        rgbA_to_RGBA          = neonemu::rgbA_to_RGBA;
        rgbA_to_BGRA          = neonemu::rgbA_to_BGRA;
        RGB_to_RGB1           = neonemu::RGB_to_RGB1;
        RGB_to_BGR1           = neonemu::RGB_to_BGR1;
        gray_to_RGB1          = neonemu::gray_to_RGB1;
        grayA_to_RGBA         = neonemu::grayA_to_RGBA;
        grayA_to_rgbA         = neonemu::grayA_to_rgbA;
        inverted_CMYK_to_RGB1 = neonemu::inverted_CMYK_to_RGB1;
        inverted_CMYK_to_BGR1 = neonemu::inverted_CMYK_to_BGR1;
    }
}  // namespace SkOpts

#endif  // SK_CPU_X86 && !SK_ENABLE_OPTIMIZE_SIZE
