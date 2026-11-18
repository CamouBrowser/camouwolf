#ifdef UNSAFE_BUFFERS_BUILD
// This file is an instruction-for-instruction transcription of ucrtbase.dll.
// Every index below is bounded by construction — the table sizes are fixed by
// the original and the indices come from bit-field extractions that cannot
// exceed them. Rewriting the accesses as spans would obscure the correspondence
// with the disassembly, which is the only thing that makes this file
// reviewable against the reference it was derived from.
#pragma allow_unsafe_buffers
#endif

// Copyright 2026 The Camouwolf Authors
// Portions derived from Holo (MPL-2.0); see camouwolf_ucrt_math.cc.

#ifndef BASE_CAMOUWOLF_CAMOUWOLF_UCRT_MATH_H_
#define BASE_CAMOUWOLF_CAMOUWOLF_UCRT_MATH_H_

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "base/camouwolf/camouwolf_ucrt_atan2_tables.h"
#include "base/camouwolf/camouwolf_ucrt_pow_tables.h"
#include "build/build_config.h"

// Every explicit fma() below must map to a single fused op and every other
// * and +/- must stay separately rounded, or the transcription stops being
// bit-exact. Verified to survive -ffp-contract=fast and -march=native.
#pragma STDC FP_CONTRACT OFF

// Bit-exact replicas of Windows 11 ucrtbase.dll scalar math, for use when a
// Camouwolf persona claims Windows while running on a non-Windows host.
//
// Why this is needed at all: Chromium reaches the HOST libm for the geometry
// that content can read back at full precision — DOMMatrix.rotate() and
// CanvasRenderingContext2D.rotate() both end in a plain sin()/cos()/atan2()
// call. On Windows those resolve to ucrtbase, on Linux to glibc, and the two
// disagree by 1 ULP on roughly 1% of angles. That difference is directly
// readable from JavaScript, so a Windows persona computing rotations with glibc
// announces itself no matter how perfect the string spoofing is.
//
// This is NOT the same question as Math.sin/cos/tan/pow. Those are computed by
// V8's own bundled LLVM-libc (v8/src/base/ieee754.cc), which has no platform
// conditionals — real Chrome produces identical bits on Windows and Linux, so
// there is nothing to correct there and correcting it would make Camouwolf differ
// from real Chrome. Only the host-libm call sites belong here.
//
// Gated on the CAMOUWOLF_UCRT_MATH environment variable, which the launcher sets
// for Windows personas. An environment variable rather than CamouwolfConfig
// because gfx needs the answer in every process (renderer, GPU, viz) and
// before any config plumbing exists, and because the environment is inherited
// by child processes for free. With the variable unset every entry point below
// falls through to the host libm, i.e. to untouched upstream behavior.

namespace camouwolf {


namespace detail {

// -1 unknown, 0 disabled, 1 enabled. A plain variable rather than a
// function-local static: this is read on the rotate path in every process, and
// a magic-static guard would put an atomic load there. The race on first use is
// benign — the computation is pure and every racer writes the same value.
inline int g_ucrt_math_mode = -1;

inline int InitMode() {
  const char* env = getenv("CAMOUWOLF_UCRT_MATH");
  int mode = (env && env[0] && env[0] != '0') ? 1 : 0;
  g_ucrt_math_mode = mode;
  return mode;
}

}  // namespace detail

inline bool UcrtMathEnabled() {
#if BUILDFLAG(IS_WIN)
  // A Windows host already IS ucrtbase; never second-guess it.
  return false;
#else
  int mode = detail::g_ucrt_math_mode;
  if (mode < 0) {
    mode = detail::InitMode();
  }
  return mode == 1;
#endif
}


inline double B2D(uint64_t b){ double d; memcpy(&d,&b,8); return d; }
inline uint64_t D2B(double d){ uint64_t b; memcpy(&b,&d,8); return b; }

/* ---- shared polynomial coefficients (0x10af10 = S1..S6, 0x10aeb0 = C1..C6) ---- */
#define S1 B2D(0xbfc5555555555555ULL)  /* -1/6            0x10af10 */
#define S2 B2D(0x3f81111111110bb3ULL)  /*                 0x10af20 */
#define S3 B2D(0xbf2a01a019e83e5cULL)  /*                 0x10af30 */
#define S4 B2D(0x3ec71de3796cde01ULL)  /*                 0x10af40 */
#define S5 B2D(0xbe5ae600b42fdfa7ULL)  /*                 0x10af50 */
#define S6 B2D(0x3de5e0b2f9a43bb8ULL)  /*                 0x10af60 */
#define C1 B2D(0x3fa5555555555555ULL)  /*  1/24           0x10aeb0 */
#define C2 B2D(0xbf56c16c16c16967ULL)  /*                 0x10aec0 */
#define C3 B2D(0x3efa01a019f4ec91ULL)  /*                 0x10aed0 */
#define C4 B2D(0xbe927e4fa17f667bULL)  /*                 0x10aee0 */
#define C5 B2D(0x3e21eeb690382eecULL)  /*                 0x10aef0 */
#define C6 B2D(0xbda907db47258aa7ULL)  /*                 0x10af00 */

/* ---- Cody-Waite (medium) reduction constants (0x10bca0..) ---- */
#define TWO_OVER_PI B2D(0x3fe45f306dc9c883ULL) /* 2/pi           0x10bca0 */
#define PIO2_HI     B2D(0x3ff921fb54442d18ULL) /* pi/2 hi        0x10bcb0 */
#define PIO2_MID    B2D(0x3c91a62633145c00ULL) /* pi/2 mid       0x10bcc0 */
#define PIO2_LO     B2D(0x397b839a252049c0ULL) /* pi/2 lo        0x10bcd0 */
#define SHIFTER     B2D(0x4338000000000000ULL) /* 1.5*2^52       0x10bce0 */

/* ---- Payne-Hanek (large) reduction constants ---- */
#define PH_MASK  0xfffffffff8000000ULL          /* 0x10bba0 */
#define PH_PI2   B2D(0x3ff921fb54442d18ULL)      /* pi/2       0x10bb90 */
#define PH_PI2HI B2D(0x3ff921fb50000000ULL)      /* pi/2 hi    0x10bbb0 */
#define PH_PI2MD B2D(0x3e5110b460000000ULL)      /* pi/2 mid   0x10bbc0 */
#define PH_PI2LO B2D(0x3c91a62633145c06ULL)      /* pi/2 lo    0x10bbd0 */

/* 2/pi bit table, byte-addressed (0x107360, little-endian words). */
inline constexpr uint64_t two_over_pi_tab[20] = {
    0x7421580cc11bf1e0ULL,0x4ba9afed7ec47e35ULL,0xc5ecf41ce7de294aULL,0xa8b5d49eeb1faf97ULL,
    0x26dd3d18fd9a797fULL,0xb47db4d9fb3c9f2cULL,0x603fbcbc462d6829ULL,0xa0ec7fe25fff7816ULL,
    0xe7d272117e2ef7e4ULL,0xf904e64758e60d4cULL,0x1213a671c09ad17dULL,0x9cfba208d7d4baedULL,
    0x4873f877ac72c4a6ULL,0x4b801924bba82746ULL,0x1586dc91b8e90937ULL,0x4107f9458eaf7aefULL,
    0x77036d8a5664f10eULL,0x54a7f09d5f47d4d3ULL,0x00028be60db93910ULL,0x0000000000000000ULL,
};

/* load 8 bytes at an arbitrary BYTE offset into the table (little-endian) */
inline uint64_t tab_u64(unsigned byteoff){
    const uint8_t *p = (const uint8_t*)two_over_pi_tab;
    uint64_t v; memcpy(&v, p+byteoff, 8); return v;
}

/* Cody-Waite reduction, ucrtbase 0xab910. Operates on a=|x| (a>=pi/4, a<2e7).
 * Returns quadrant n&3; *hi = reduced value, *lo = tail. */
inline int reduce_medium(double a, double *hi, double *lo){
    double fn = fma(a, TWO_OVER_PI, SHIFTER);
    fn = fn - SHIFTER;                       /* round(a*2/pi) as double */
    int n = (int)fn;                         /* vcvttpd2dq */
    double r0  = fma(-fn, PIO2_HI, a);       /* a - fn*pio2_hi (exact fma) */
    double p   = fn * PIO2_MID;
    double perr= fma(fn, PIO2_MID, -p);      /* rounding error of fn*pio2_mid */
    double t1  = r0 - p;
    double t2  = r0 - t1;
    double t3  = t2 - p;
    double r1  = fma(-fn, PIO2_MID, r0);
    double s1  = t1 - r1;
    double s2  = s1 + t3;
    double s3  = s2 - perr;
    *hi = r1;
    *lo = fma(-fn, PIO2_LO, s3);
    return n & 3;
}

/* Payne-Hanek reduction, ucrtbase 0xab710. Operates on a=|x| (a>=2e7, finite).
 * Returns quadrant n&3; *hi = reduced value, *lo = tail. */
inline int reduce_large(double a, double *hi, double *lo){
    uint64_t bits = D2B(a);
    int64_t e = (int64_t)(bits >> 0x34) - 0x3ff;        /* unbiased exponent */
    int64_t off = 0x86 - (e >> 3);                       /* byte offset */
    uint64_t signif = ((bits << 12) >> 12) | (1ULL << 52);

    uint64_t w0 = tab_u64((unsigned)off);
    uint64_t w1 = tab_u64((unsigned)off + 8);
    uint64_t w2 = tab_u64((unsigned)off + 16);
    unsigned em = (unsigned)(e & 7);

    /* 192-bit product signif * (w0:w1:w2) -> r8(lo), r9(mid), r10(hi) */
    __uint128_t p0 = (__uint128_t)signif * w0;
    uint64_t r8  = (uint64_t)p0;
    uint64_t hiacc = (uint64_t)(p0 >> 64);
    __uint128_t p1 = (__uint128_t)signif * w1;
    uint64_t lo1 = (uint64_t)p1, hi1 = (uint64_t)(p1 >> 64);
    __uint128_t s = (__uint128_t)lo1 + hiacc;            /* add + adc */
    uint64_t r9 = (uint64_t)s;
    hi1 += (uint64_t)(s >> 64);
    __uint128_t p2 = (__uint128_t)signif * w2;
    uint64_t r10 = hi1 + (uint64_t)p2;                   /* only low of p2 */

    unsigned shift = (unsigned)(54 - (int)em);
    uint64_t rax = r10 >> shift;
    uint64_t round_cf = (shift ? (r10 >> (shift-1)) & 1 : 0);  /* CF from shr */
    uint64_t rdx = 0;
    if (round_cf){
        r10 = ~r10; r9 = ~r9; r8 = ~r8;
        rdx = 0x8000000000000000ULL;
    }
    rax += round_cf;                                     /* adc rax,0 */
    int n = (int)(rax & 3);

    /* keep the fractional bits: clear top (em+10) bits of r10 */
    unsigned cl = em + 10;
    r10 = (r10 << cl) >> cl;
    int64_t r11 = (int64_t)cl - 0x40;                    /* rcx-0x40 */

    /* normalize: find highest set bit */
    int idx;
    if (r10 != 0){
        idx = 63 - __builtin_clzll(r10);
    } else {
        r10 = r9; r9 = r8; r8 = 0;
        r11 -= 0x40;
        idx = 63 - __builtin_clzll(r10);
    }
    r11 += idx;
    int rc = idx - 0x34;
    if (rc < 0){
        unsigned c = (unsigned)(-rc);
        uint64_t rax2 = r9;
        r10 <<= c;
        r9  <<= c;
        unsigned c2 = (unsigned)(0x40 - c);              /* -(c-0x40) */
        rax2 >>= c2;
        r10 |= rax2;
        r8 >>= c2;
        r9 |= r8;
    } else if (rc > 0){
        unsigned c = (unsigned)rc;
        uint64_t r8b = r10;
        r10 >>= c;
        r9  >>= c;
        unsigned c2 = (unsigned)(0x40 - c);
        r8b <<= c2;
        r9 |= r8b;
    }
    /* assemble hi_frac double (r10) */
    int64_t bexp = r11 + 0x3ff;
    r10 &= ~(1ULL << 0x34);                              /* btr bit 52 */
    r10 |= rdx;
    r10 |= ((uint64_t)bexp) << 0x34;
    double hf = B2D(r10);
    /* assemble lo_frac double (r9); r11 now holds bexp (was updated by +0x3ff) */
    int idx2 = 63 - __builtin_clzll(r9);
    unsigned c3 = (unsigned)(0x40 - idx2);               /* -(bsr(r9)-0x40) */
    uint64_t r9n = r9 << c3;
    r9n >>= 0xc;
    int64_t rc2 = (int64_t)c3 + 0x34;
    int64_t bexp2 = bexp - rc2;
    r9n |= rdx;
    r9n |= ((uint64_t)bexp2) << 0x34;
    double lf = B2D(r9n);

    /* multiply (hf, lf) by pi/2 with a split product (Dekker) */
    double xmm4 = B2D(D2B(hf) & PH_MASK);
    double xmm0 = hf - xmm4;
    double xmm5 = hf * PH_PI2;
    double xmm3 = xmm4 * PH_PI2HI;
    xmm3 = xmm3 - xmm5;
    xmm3 = fma(xmm0, PH_PI2HI, xmm3);
    xmm3 = fma(xmm4, PH_PI2MD, xmm3);
    xmm3 = fma(xmm0, PH_PI2MD, xmm3);
    double xmm4b = lf * PH_PI2;
    xmm4b = fma(hf, PH_PI2LO, xmm4b);
    xmm3 = xmm3 + xmm4b;
    double H = xmm5 + xmm3;
    double L = (xmm5 - H) + xmm3;
    *hi = H; *lo = L;
    return n & 3;
}

/* dispatch reduction for a=|x| >= pi/4, finite */
inline int reduce(double a, double *hi, double *lo){
    if (D2B(a) >= 0x417312d000000000ULL /* 2e7 */)
        return reduce_large(a, hi, lo);
    return reduce_medium(a, hi, lo);
}

/* sin kernel for n even (S-poly), ucrtbase 0xabebf; hi=r, lo=t */
inline double kernel_sin(double r, double t){
    double z = r*r;
    double p = fma(fma(fma(fma(S6, z, S5), z, S4), z, S3), z, S2);
    double v = r*z;                 /* r^3 */
    double w = 0.5*t;
    double q = w - v*p;
    q = z*q;
    q = q - t;
    q = fma(-v, S1, q);
    return r - q;
}

/* cos kernel for n odd in sin (C-poly), ucrtbase 0xabf20; hi=r, lo=t */
inline double kernel_cos(double r, double t){
    double z = r*r;
    double half_z = z*0.5;
    double hi1 = 1.0 - half_z;
    double err = (1.0 - hi1) - half_z;
    err = fma(-r, t, err);
    double zz = z*z;
    double p = fma(fma(fma(fma(fma(C6, z, C5), z, C4), z, C3), z, C2), z, C1);
    return fma(p, zz, err) + hi1;
}

inline double UcrtSin(double x){
    uint64_t xb = D2B(x);
    uint64_t ab = xb & 0x7fffffffffffffffULL;
    double a = B2D(ab);
    if (ab < 0x3fe921fb54442d18ULL){          /* |x| < pi/4: small paths on signed x */
        if (ab < 0x3e40000000000000ULL) return x;                 /* |x|<2^-27 */
        if (ab < 0x3f20000000000000ULL){                          /* <1.22e-4 */
            double x3 = x*x*x;
            return fma(-x3, B2D(0x3fc5555555555555ULL), x);        /* x - x^3/6 */
        }
        double z = x*x;                                           /* direct S-poly */
        double p = fma(fma(fma(fma(fma(S6,z,S5),z,S4),z,S3),z,S2),z,S1);
        double v = x*z;
        return fma(v, p, x);
    }
    if (ab >= 0x7ff0000000000000ULL) return x - x;                /* inf/nan */
    double hi, lo;
    int n = reduce(a, &hi, &lo);
    double res = (n & 1) ? kernel_cos(hi, lo) : kernel_sin(hi, lo);
    uint64_t sgn = (xb & 0x8000000000000000ULL);
    if (n & 2) sgn ^= 0x8000000000000000ULL;
    return B2D(D2B(res) ^ sgn);
}

inline double UcrtCos(double x){
    uint64_t xb = D2B(x);
    uint64_t ab = xb & 0x7fffffffffffffffULL;
    double a = B2D(ab);
    if (ab <= 0x3fe921fb54442d18ULL){          /* |x| <= pi/4 (cos entry uses jg) */
        if (ab < 0x3e40000000000000ULL) return 1.0;                   /* tiny */
        if (ab < 0x3f20000000000000ULL){                              /* <1.22e-4 */
            double h = a*0.5;
            return fma(-a, h, 1.0);                                    /* 1 - 0.5 a^2 */
        }
        double z = a*a;                                               /* C-poly direct */
        double p = fma(fma(fma(fma(fma(C6,z,C5),z,C4),z,C3),z,C2),z,C1);
        double r = fma(p, z, -0.5);
        return fma(r, z, 1.0);
    }
    if (ab >= 0x7ff0000000000000ULL) return x - x;                    /* inf/nan */
    double hi, lo;
    int n = reduce(a, &hi, &lo);
    double res = (n & 1) ? kernel_sin(hi, lo) : kernel_cos(hi, lo);
    uint64_t sgn = ((n + 1) & 2) ? 0x8000000000000000ULL : 0;
    return B2D(D2B(res) ^ sgn);
}

/* tan constants */
#define TP1 B2D(0x3fd7d50f6638564aULL) /* P1 0x10bf50 */
#define TP2 B2D(0xbf977c24c7569abbULL) /* P2 0x10bf60 */
#define TP3 B2D(0x3f2d5daf289c385aULL) /* P3 0x10bf70 */
#define TQ1 B2D(0x3ff1dfcb8caa40b8ULL) /* Q1 0x10bf80 */
#define TQ2 B2D(0xbfe08046499eb90fULL) /* Q2 0x10bf90 */
#define TQ3 B2D(0x3f9ab0f4f80a0acfULL) /* Q3 0x10bfa0 */
#define TQ4 B2D(0xbf2e7517ef6d98f8ULL) /* Q4 0x10bfb0 */
#define TPI4    B2D(0x3fe921fb54442d18ULL) /* pi/4      0x10bfd0 */
#define TPI4TL  B2D(0x3c81a62633145c06ULL) /* pi/4 tail 0x10bfe0 */
#define TAN_MASK 0xffffffff00000000ULL     /* 0x10bfc0 */

/* P/Q core + octant/cot fixup, shared by tan.
 * small!=0: direct/shifted small path (ucrtbase 0xacf3c) fuses T=fma(pq,hi,hi).
 * small==0: reduced main path (ucrtbase 0xad062) does corr=fma(pq,hi,lo);T=hi+corr. */
inline double tan_core(double hi, double lo, int n, double flag, int small){
    double W = fma(lo*hi, 2.0, hi*hi);       /* full squared arg */
    double P = fma(fma(TP3, W, TP2), W, TP1);
    double Q = fma(fma(fma(TQ4, W, TQ3), W, TQ2), W, TQ1);
    double pq = P / Q;
    pq = pq * W;
    double corr, T;
    if (small){
        corr = pq * hi;                      /* only used if a cot path were hit (it isn't) */
        T = fma(pq, hi, hi);                 /* vfmadd132sd xmm0,xmm0,xmm3 */
    } else {
        corr = fma(pq, hi, lo);
        T = hi + corr;                       /* tan(reduced) */
    }
    if (flag != 0.0){
        if (n & 1){                          /* 0xad100 */
            double d = T - 1.0;
            double r = T / d;
            r = fma(r, 2.0, -1.0);           /* 2T/(T-1) - 1 */
            return r * flag;
        } else {                             /* 0xad0e6 */
            double d = T + 1.0;
            double r = T / d;
            r = fma(-r, 2.0, 1.0);           /* 1 - 2T/(T+1) */
            return r * flag;
        }
    } else {
        if (!(n & 1)) return T;              /* even: tan */
        /* odd, no pi/4 shift: cotangent -1/T with correction (0xad127) */
        double That = B2D(D2B(T) & TAN_MASK);
        double xmm4 = That - hi;
        xmm4 = corr - xmm4;
        double inv = -1.0 / T;
        double invhat = B2D(D2B(inv) & TAN_MASK);
        double r = fma(That, invhat, 1.0);
        r = fma(xmm4, invhat, r);
        r = fma(r, inv, invhat);
        return r;
    }
}

inline double UcrtTan(double x){
    uint64_t xb = D2B(x);
    uint64_t ab = xb & 0x7fffffffffffffffULL;
    double a = B2D(ab);
    double hi, lo; int n; double flag;
    if (ab >= 0x7ff0000000000000ULL) return x - x;                   /* inf/nan */
    if (ab < 0x3fe921fb54442d18ULL){          /* |x| < pi/4 */
        if (ab < 0x3e40000000000000ULL) return x;                    /* tiny */
        if (ab > 0x3fe5c28f5c28f5c3ULL){       /* |x| > 0.68: pi/4 pre-shift (0xad1c0) */
            /* the small-path shift works on signed x */
            if (x >= 0.0){ flag = 1.0; hi = TPI4 - x; }
            else         { flag = -1.0; hi = TPI4 + x; }
            hi = hi + TPI4TL;                  /* + pi/4 tail (xmm6) */
            lo = 0.0; n = 0;
            return tan_core(hi, lo, n, flag, 1);
        }
        /* 7.45e-9 <= |x| <= 0.68: direct P/Q, hi=x, lo=0, flag=0, n=0 */
        return tan_core(x, 0.0, 0, 0.0, 1);
    }
    /* |x| >= pi/4: reduce, then pi/4-shift decision (0xacfd0/0xad00e) */
    n = reduce(a, &hi, &lo);
    if (hi > 0.68){
        flag = 1.0;
        double h = TPI4 - hi;
        double l = TPI4TL - lo;
        hi = h + l; lo = 0.0;
    } else if (hi < -0.68){
        flag = -1.0;
        double h = TPI4 + hi;
        double l = TPI4TL + lo;
        hi = h + l; lo = 0.0;
    } else {
        flag = 0.0;
    }
    double res = tan_core(hi, lo, n, flag, 0);
    uint64_t sgn = xb & 0x8000000000000000ULL;    /* tan is odd; sign of x */
    return B2D(D2B(res) ^ sgn);
}


/* ==========================================================================
 * pow — ucrtbase.dll export `pow`, dispatch stub .text 0x766e0 -> FMA3 body at
 * 0x5e990. SpiderMonkey's ecmaPow calls std::pow on every platform, so this is
 * what closes the Math.pow half of the leak.
 * ========================================================================== */

using detail::kUcrtPowExpTab;
using detail::kUcrtPowLogTab;

inline double bits2d(uint64_t b) { double d; memcpy(&d, &b, 8); return d; }
inline uint64_t d2bits(double d) { uint64_t b; memcpy(&b, &d, 8); return b; }

// .rdata constants, named by their RVA in ucrtbase.dll.
#define C_1020e8 1.0                       /* 0x3ff0000000000000 */
#define SIGNMASK 0x8000000000000000ULL     /* 0x103e00 */
#define C_103e10 (-1.0)
#define C_102d98 bits2d(0x3fe62e42fefa3800ULL) /* ln2 hi   */
#define C_103df0 bits2d(0x3d2ef35793c76730ULL) /* ln2 lo   */
#define C_103e18 (-0.5)
#define C_102d88 bits2d(0x3fe555555529a47aULL)
#define C_103de8 bits2d(0x3fe999999959554eULL)
#define C_103e20 bits2d(0x3ff0002b8b263fc3ULL)
#define C_102d80 bits2d(0x3ff2495b9b4845e9ULL)
#define C_103e28 bits2d(0x3fe0000000000006ULL)
#define C_102d90 bits2d(0x3fe5555555555560ULL)
#define C_104ec8 bits2d(0x40771547652b82feULL) /* 256/ln2  */
#define C_104ed0 bits2d(0x4238000000008000ULL) /* shifter  */
#define C_103e38 bits2d(0x3f662e42fefc0000ULL) /* ln2/256 hi */
#define C_104eb8 bits2d(0x3d2c610ca86c3899ULL) /* ln2/256 lo */
#define C_104eb0 bits2d(0x3fc555555555543cULL) /* 1/6      */
#define C_103e30 bits2d(0x3fdffffffffffdbdULL) /* 1/2      */
#define C_104ed8 bits2d(0x3f81111167a4b553ULL) /* 1/120    */
#define C_104ec0 bits2d(0x3fa55555cf16e1edULL) /* 1/24     */
#define C_105a20 bits2d(0x7f00000000000000ULL) /* 2^1009   */
#define C_105a18 bits2d(0x0010000000000000ULL) /* 2^-1022  */
#define C_115cc0 bits2d(0x4330000000000000ULL) /* 2^52     */

// The overflow / underflow tails call ucrtbase's _handle_error helpers, which
// set errno and return the already-computed value. Value-wise they are the
// identity, so the arithmetic below is the whole story.

// Core: |x| is a positive finite double (subnormals pre-scaled by the caller),
// y is finite with exponent in the range the fast path screens for. `negate` is
// 0x80000 when the result must be sign-flipped (x < 0 raised to an odd integer).
inline double UcrtPowKernel(uint64_t xb, double y, uint64_t negate) {
  // ---- ln(|x|) in double-double -------------------------------------------
  uint64_t t = 0xc0196aab00000000ULL + xb;
  uint64_t hi = t & 0xfff0000000000000ULL;
  uint64_t xr_bits = xb - hi;
  int32_t k = (int32_t)((int64_t)t >> 52);
  uint32_t idx = (uint32_t)((t >> 45) & 0x7f);

  double kd = (double)k;
  double xr = bits2d(xr_bits);
  double T0 = bits2d(kUcrtPowLogTab[idx][0]);
  double T2 = bits2d(kUcrtPowLogTab[idx][2]);
  double T3 = bits2d(kUcrtPowLogTab[idx][3]);

  double r = fma(T0, xr, C_103e10);
  double a1 = kd * C_102d98 + T2;
  double b0 = r * C_103e18;
  double p = b0 * r;
  double pe = fma(b0, r, -p);
  double c1 = kd * C_103df0 + T3;
  double s1 = a1 + r;
  double s1e = (a1 - s1) + r;
  double e1 = pe + (c1 + s1e);
  double q2 = r * C_102d88;
  double s2 = p + s1;
  double s2e = (s1 - s2) + p;
  double e2 = e1 + s2e;
  double q1 = r * C_103e20;
  double d0 = C_103de8 - q2;
  double d1 = q1 - C_102d80;
  double d2 = d1 * p;
  double d3 = r * C_103e28 - C_102d90;
  double d4 = d0 + d2;
  double d5 = d4 * p;
  double d6 = d5 + d3;
  double pr = p * r;
  double lo = e2 + d6 * pr;
  double L = lo + s2;

  // ---- z = y * ln(|x|), carrying the low word ------------------------------
  double z = L * y;
  uint32_t zexp = (uint32_t)((d2bits(z) >> 52) & 0x7ff);
  double ze = fma(y, L, -z);
  double zlo = ze + ((s2 - L) + lo) * y;

  uint32_t rel = zexp - 0x3c9;
  if (rel >= 0x3f) {
    if (rel >= 0x80000000u) {
      // |z| < 2^-54: exp(z) rounds to 1 + z.
      double v = z + C_1020e8;
      return negate ? -v : v;
    }
    if (zexp >= 0x409) {
      // |z| >= 1024: unconditional overflow / underflow.
      double v = (d2bits(z) & SIGNMASK) ? 0.0 : INFINITY;
      return negate ? -v : v;
    }
    zexp = 0;  // |z| in [2^9, 2^10): result may be subnormal or overflow
  }

  // ---- exp(z) --------------------------------------------------------------
  double shifted = z * C_104ec8 + C_104ed0;
  uint64_t nx = d2bits(shifted) >> 16;
  double nd = (double)(int32_t)(uint32_t)nx;
  double rr = ((z - nd * C_103e38) + nd * C_104eb8) + zlo;

  double rr2 = rr * rr;
  double poly_hi = (rr * C_104eb0 + C_103e30) * rr2;
  double poly_lo = (rr * C_104ed8 + C_104ec0) * (rr2 * rr2);
  uint32_t i = (uint32_t)(nx & 0xff);
  uint64_t scaleBits = ((negate + nx) << 44) + kUcrtPowExpTab[i][1];
  double P = (poly_hi + (rr + bits2d(kUcrtPowExpTab[i][0]))) + poly_lo;

  if (zexp != 0) {
    double s = bits2d(scaleBits);
    return P * s + s;
  }

  if (nx & 0x80000000ULL) {
    // Underflow direction: build the result one binade up, then scale down so
    // gradual underflow rounds exactly the way the hardware would.
    double one = C_1020e8;
    double tiny = C_105a18;
    uint64_t rax = 0x3fe0000000000000ULL + scaleBits;
    double s = bits2d(rax);
    double prod = s * P;
    double v = s + prod;
    if (fabs(v) < one) {
      double sgn = (v < 0.0) ? C_103e10 : one;
      double h = sgn + v;
      double acc = ((s - v) + prod) + ((sgn - h) + v);
      double sum = acc + h;
      v = sum - sgn;
      if (v == 0.0) v = bits2d(rax & SIGNMASK);
    }
    return v * tiny;
  }
  // Overflow direction: same trick, one binade down.
  uint64_t rax = 0xc0f0000000000000ULL + scaleBits;
  double s = bits2d(rax);
  double v = s * P + s;
  return v * C_105a20;
}

inline double UcrtPow(double x, double y) {
  uint64_t xb = d2bits(x);
  uint64_t yb = d2bits(y);
  uint64_t xhi = xb >> 52;
  uint64_t yhi = yb >> 52;

  // Fast screen (the dispatch prologue): x positive normal, |y| in a range
  // where neither the log nor the exp can degenerate.
  if ((uint32_t)(xhi - 1) < 0x7fe && (uint32_t)((yhi & 0x7ff) - 0x3be) < 0x80) {
    return UcrtPowKernel(xb, y, 0);
  }

  uint64_t y2 = yb + yb;
  if (y2 - 1 >= 0xffdfffffffffffffULL) {
    // y is zero, infinity or NaN.
    if (y2 == 0) {
      uint64_t a = (xb ^ 0x0008000000000000ULL) << 1;
      return a > 0xfff0000000000000ULL ? x + y : 1.0;  // pow(NaN, 0) -> NaN+0
    }
    if (xb == 0x3ff0000000000000ULL) {
      uint64_t a = (yb ^ 0x0008000000000000ULL) << 1;
      return a <= 0xfff0000000000000ULL ? 1.0 : x + y;
    }
    uint64_t x2 = xb + xb;
    if (x2 > 0xffe0000000000000ULL || y2 > 0xffe0000000000000ULL) return x + y;  // NaN
    if (x2 == 0x7fe0000000000000ULL) return 1.0;                                 // pow(+-1, +-inf)
    int ypos = (int)((yb >> 63) ^ 1);
    int xsmall = (x2 < 0x7fe0000000000000ULL);
    return (xsmall != ypos) ? y * y : 0.0;
  }

  uint64_t negate = 0;
  uint64_t x2 = xb + xb;
  if (x2 - 1 >= 0xffdfffffffffffffULL) {
    // x is zero, infinity or NaN; y is finite and non-zero.
    double xx = x * x;
    int oddInt = 0;
    if (xb & SIGNMASK) {
      uint32_t ye = (uint32_t)(yhi & 0x7ff);
      uint32_t d = ye - 0x3ff;
      if (d <= 0x34) {
        uint64_t mask = 1ULL << (0x433 - ye);
        if (((mask - 1) & yb) == 0 && (mask & yb) != 0) {
          xx = -xx;
          oddInt = 1;
        }
      }
    }
    if (x2 == 0) {                          // x == +-0
      if ((yb & SIGNMASK) == 0) return xx;  // y > 0 -> +-0
      // y < 0: divide-by-zero. oddInt is only ever set for a negative base, so
      // it alone carries the sign (x < 0 is false for -0.0).
      return oddInt ? -INFINITY : INFINITY;
    }
    if ((yb & SIGNMASK) == 0) return xx;            // x == +-inf, y > 0
    return 1.0 / xx;                                // x == +-inf, y < 0 -> +-0
  }

  // Both finite and non-zero. Resolve the sign of a negative base.
  uint64_t xabs = xb;
  if (xb & SIGNMASK) {
    uint32_t ye = (uint32_t)(yhi & 0x7ff);
    int kind;  // 1 = odd integer, 2 = even integer
    if (ye < 0x3ff) return B2D(0xfff8000000000000ULL);  // domain err: ucrt -NaN
    if (ye > 0x433) {
      kind = 2;
    } else {
      uint64_t mask = 1ULL << (0x433 - ye);
      if (((mask - 1) & yb) != 0) return B2D(0xfff8000000000000ULL);  // ucrt -NaN
      kind = (mask & yb) ? 1 : 2;
    }
    xabs = xb & 0x7fffffffffffffffULL;
    xhi = xhi & 0x7ff;
    negate = (kind == 1) ? 0x80000ULL : 0;
  }

  uint32_t ye = (uint32_t)(yhi & 0x7ff);
  if ((uint32_t)(ye - 0x3be) >= 0x80) {
    if (xabs == 0x3ff0000000000000ULL) return 1.0;  // pow(+-1, y) == 1
    if (ye < 0x3be) {
      // |y| < 2^-65: the result is within one ULP of 1.
      return (xabs <= 0x3ff0000000000000ULL) ? 1.0 - y : y + 1.0;
    }
    // |y| >= 2^62: overflow or underflow, decided by |x| vs 1 and sign(y).
    int xbig = (xabs > 0x3ff0000000000000ULL);
    int ypos = (int)(yhi < 0x800);
    return (xbig != ypos) ? 0.0 : INFINITY;
  }

  if ((xhi & 0x7ff) == 0) {
    // Subnormal |x|: scale into the normal range and correct the exponent.
    double scaled = bits2d(xabs) * C_115cc0;
    xabs = (d2bits(scaled) & 0x7fffffffffffffffULL) - 0x0340000000000000ULL;
  }
  return UcrtPowKernel(xabs, y, negate);
}

/* ==========================================================================
 * sinf / cosf — ucrtbase.dll .text 0xac360 / 0xa7f70 (FMA3 bodies).
 *
 * Moz2D's `Matrix::Rotation(Float)` — which is what CanvasRenderingContext2D
 * .rotate() runs — uses the FLOAT libm, not the double one, so closing the
 * double sin/cos leak alone still left ~1% of canvas rotation matrices one
 * float-ULP away from Windows.
 * ========================================================================== */

#define F_2OPI B2D(0x3fe45f306dc9c883ULL)  /* 2/pi                 */
#define F_PIO2H B2D(0x3ff921fb54400000ULL) /* pi/2, high half      */
#define F_PIO2L B2D(0x3dd0b4611a626331ULL) /* pi/2, low half       */
#define F_PIO2 B2D(0x3ff921fb54442d18ULL)  /* pi/2 (Payne-Hanek)   */
#define F_SIXTH B2D(0x3fc5555555555555ULL)
#define FS0 B2D(0xbf2a01a01a01a01aULL)
#define FS1 B2D(0x3ec71de3a556c734ULL)
#define FS2 B2D(0x3f81111111111111ULL)
#define FS3 B2D(0xbfc5555555555555ULL)
#define FC0 B2D(0x3efa01a01a01a019ULL)
#define FC1 B2D(0xbe927e4fb7789f5cULL)
#define FC2 B2D(0xbf56c16c16c16c16ULL)
#define FC3 B2D(0x3fa5555555555555ULL)

inline double f_sin_poly(double x) {
  double x2 = x * x;
  double p = FS0;
  p = fma(x2, FS1, p);
  p = fma(p, x2, FS2);
  p = fma(p, x2, FS3);
  return fma(p, x * x2, x);
}

// The two callers build the leading 1 - x^2/2 differently (sinf fuses it, cosf
// does not), and that difference is observable, so keep them apart.
inline double f_cos_poly_tail(double x2, double head) {
  double p = FC0;
  p = fma(x2, FC1, p);
  p = fma(p, x2, FC2);
  p = fma(p, x2, FC3);
  return fma(p, x2 * x2, head);
}
inline double f_cos_poly_fused(double x) {
  double x2 = x * x;
  return f_cos_poly_tail(x2, fma(x2, -0.5, 1.0));
}
inline double f_cos_poly_split(double x) {
  double x2 = x * x;
  return f_cos_poly_tail(x2, 1.0 - x2 * 0.5);
}

// Cody-Waite: quadrant + reduced argument for pi/4 < |x| < the Payne-Hanek
// threshold. `ax` must already be |x| as a double.
inline double f_reduce_medium(double ax, uint64_t* quadrant) {
  double n = fma(F_2OPI, ax, 0.5);
  int32_t ni = (int32_t)n;  // vcvttpd2dq
  *quadrant = (uint64_t)(int64_t)ni & 3;
  double nd = (double)ni;
  double hi = fma(-nd, F_PIO2H, ax);
  return hi - nd * F_PIO2L;
}

// Payne-Hanek, ucrtbase .text 0xac490: multiply the mantissa by the right
// window of the 2/pi bit table, keep the fractional part, renormalise, scale by
// pi/2. Same table as the double-precision reduction above.
inline double f_reduce_large(double ax, uint64_t* quadrant) {
  uint64_t xb = D2B(ax);
  uint64_t mant = xb;
  int64_t e = (int64_t)(xb >> 52) - 0x3ff;
  int64_t off = -((e >> 3) - 0x86);

  uint64_t t0 = tab_u64((unsigned)off);
  mant = (mant << 12) >> 12;
  mant |= 1ULL << 52;
  uint64_t t1 = tab_u64((unsigned)off + 8);
  uint64_t t2 = tab_u64((unsigned)off + 16);

  unsigned __int128 p0 = (unsigned __int128)t0 * mant;
  uint64_t r8 = (uint64_t)p0;
  uint64_t carry = (uint64_t)(p0 >> 64);
  unsigned __int128 p1 = (unsigned __int128)t1 * mant + carry;
  uint64_t r9 = (uint64_t)p1;
  uint64_t r10 = (uint64_t)(p1 >> 64);
  r10 += (uint64_t)((unsigned __int128)t2 * mant);

  uint64_t elow = (uint64_t)e & 7;
  unsigned sh = (unsigned)(0x36 - elow);
  uint64_t q = r10 >> sh;
  uint64_t sign = 0;
  if ((r10 >> (sh - 1)) & 1) {  // the bit shifted out: round up == negate
    r10 = ~r10;
    r9 = ~r9;
    r8 = ~r8;
    sign = 0x8000000000000000ULL;
    q += 1;
  }
  *quadrant = q & 3;

  unsigned cl = (unsigned)(elow + 10);
  r10 = (r10 << cl) >> cl;
  int64_t exp = (int64_t)cl - 64;

  int bit;
  if (r10 != 0) {
    bit = 63 - __builtin_clzll(r10);
  } else {
    r10 = r9;
    r9 = r8;
    r8 = 0;
    exp -= 64;
    bit = r10 ? 63 - __builtin_clzll(r10) : 0;
  }
  exp += bit;

  int64_t shift = (int64_t)bit - 0x34;
  if (shift > 0) {
    unsigned s = (unsigned)shift;
    uint64_t keep = r10;
    r10 >>= s;
    r9 >>= s;
    r9 |= keep << (64 - s);
  } else if (shift < 0) {
    unsigned s = (unsigned)(-shift);
    uint64_t hi9 = r9;
    r10 <<= s;
    r9 <<= s;
    r10 |= hi9 >> (64 - s);
    r9 |= r8 >> (64 - s);
  }

  exp += 0x3ff;
  r10 &= ~(1ULL << 52);
  r10 |= sign;
  r10 |= (uint64_t)exp << 52;
  return B2D(r10) * F_PIO2;
}



/* ==========================================================================
 * atan2 — ucrtbase.dll .text 0x4d400 -> FMA3 body at 0x4d420.
 *
 * DOMMatrix.rotateFromVector() / SVGMatrix.rotateFromVector() run the host
 * atan2 on doubles and hand the matrix straight to content; canvas arcTo and
 * ellipse use it for their sweep angles. glibc and ucrtbase disagree by 1 ULP
 * on ~0.3% of argument pairs.
 * ========================================================================== */

#define A_PIO2H B2D(0x3ff921fb54442d18ULL) /* pi/2, high   0x102900 */
#define A_PIO2L B2D(0x3c91a62633145c07ULL) /* pi/2, low    0x101f70 */
#define A_PIH B2D(0x400921fb50000000ULL)   /* pi, high     0x101f80 */
#define A_PIL B2D(0x3e6110b4611a6263ULL)   /* pi, low      0x101f78 */
#define A_PI B2D(0x400921fb54442d18ULL)    /* pi           0x102908 */
#define A_TINY B2D(0x3e45798ee2308c3aULL)  /* 1e-8         0x101f88 */
#define A_SPLIT B2D(0x3fb0000000000000ULL) /* 1/16         0x102150 */
#define A_1_256 B2D(0x3f70000000000000ULL) /* 1/256        0x101f60 */
#define A_256 256.0                        /*              0x1028f8 */
#define A_P1 B2D(0x3fc99992b9802d7dULL)    /*              0x101f68 */
#define A_P2 B2D(0x3fd5555555550877ULL)    /*              0x1028e8 */
#define A_S1 B2D(0x3fd5555555555538ULL)    /*              0x101f98 */
#define A_S2 B2D(0x3fc99999999643a3ULL)    /*              0x101fb0 */
#define A_S3 B2D(0x3fc2492482bd6be1ULL)    /*              0x101f90 */
#define A_S4 B2D(0x3fbc718839b8c4ebULL)    /*              0x101fa0 */
#define A_S5 B2D(0x3fb70c3192d751c2ULL)    /*              0x101fa8 */

// ucrtbase 0x4def0: multiply by exactly 2^1024, handling subnormals without an
// intermediate overflow. Applied to both operands, so the ratio is unchanged.
inline double UcrtAtan2Normalize(double v) {
  uint64_t b = D2B(v);
  if (b & 0x7ff0000000000000ULL) {
    return B2D(b + 0x4000000000000000ULL);
  }
  double t = B2D(b | 0x4010000000000000ULL);
  return (b >> 63) ? (t + 4.0) : (t - 4.0);
}

inline double UcrtAtan2(double y, double x) {
  uint64_t xb = D2B(x);
  uint64_t yb = D2B(y);
  uint64_t xabs = xb & 0x7fffffffffffffffULL;
  uint64_t yabs = yb & 0x7fffffffffffffffULL;
  uint64_t xsign = xb & SIGNMASK;
  uint64_t ysign = yb & SIGNMASK;
  const uint64_t kInf = 0x7ff0000000000000ULL;

  if (xabs > kInf) return B2D(xb | 0x0008000000000000ULL);  // x is NaN
  if (yabs > kInf) return B2D(yb | 0x0008000000000000ULL);  // y is NaN

  if (yabs == 0) {
    if (!xsign) return y;                      // +-0 / +x  ->  +-0
    return ysign ? -A_PI : A_PI;               // +-0 / -x  ->  +-pi
  }
  if (xabs == 0) return ysign ? -A_PIO2H : A_PIO2H;

  if (xabs == kInf && yabs == kInf) {
    double q = xsign ? B2D(0x4002d97c7f3321d2ULL)   // 3pi/4
                     : B2D(0x3fe921fb54442d18ULL);  // pi/4
    return ysign ? -q : q;
  }

  int32_t ex = (int32_t)((xb >> 52) & 0x7ff);
  int32_t ey = (int32_t)((yb >> 52) & 0x7ff);
  if (ex < 0x3fd && ey < 0x3fd) {
    // Both tiny: scale each by exactly 2^1024, which leaves the ratio alone but
    // brings subnormals into the normal range. (ucrtbase 0x4def0.)
    x = UcrtAtan2Normalize(x);
    y = UcrtAtan2Normalize(y);
    xb = D2B(x);
    yb = D2B(y);
    ex = (int32_t)((xb >> 52) & 0x7ff);
    ey = (int32_t)((yb >> 52) & 0x7ff);
  }

  int32_t d = ey - ex;
  if (d > 0x38) return ysign ? -A_PIO2H : A_PIO2H;  // |y| >> |x|
  if (d < -0x38 && xsign) return ysign ? -A_PI : A_PI;
  if (d < -0x1c && !xsign) {
    // |y| << |x|: atan2 IS the quotient. ucrtbase 0xf3ee8 computes a subnormal
    // result by scaling through 2^100 first, which double-rounds — reproduce
    // that rather than the single-rounded y/x.
    if (d < -0x432) return ysign ? -0.0 : 0.0;
    if (d < -0x3fe) return ((y * B2D(0x4630000000000000ULL)) / x) *
                           B2D(0x39b0000000000000ULL);
    return y / x;
  }

  // Main path: work with the magnitudes, smaller over larger.
  double a = B2D(yabs);
  double b = B2D(xabs);
  int swapped = 0;
  if (a > b) {
    double t = a;
    a = b;
    b = t;
    swapped = 1;
  }
  double ratio = a / b;

  double hi;  // atan(k/256) for the table point, 0 on the small-ratio path
  double lo;  // the rest
  if (ratio <= A_SPLIT) {
    hi = 0.0;
    if (A_TINY > ratio) {
      lo = ratio;
    } else {
      double r2 = ratio * ratio;
      // Exact residual of a - ratio*b, so the polynomial sees the true tail.
      double bhi = B2D(D2B(b) & 0xffffffff00000000ULL);
      double rhi = B2D(D2B(ratio) & 0xffffffff00000000ULL);
      double blo = b - bhi;
      double res = a;
      res = fma(-bhi, rhi, res);
      res = fma(-rhi, blo, res);
      res = fma(-b, ratio - rhi, res);
      double p = A_S5;
      p = fma(-p, r2, A_S4);
      p = fma(-p, r2, A_S3);
      p = fma(-p, r2, A_S2);
      p = fma(-p, r2, A_S1);
      double corr = res / b;
      corr = fma(-(r2 * ratio), p, corr);
      lo = corr + ratio;
    }
  } else {
    double idxd = fma(A_256, ratio, 0.5);
    int32_t k = (int32_t)idxd;   // vcvttsd2si
    uint32_t i = (uint32_t)(k - 0x10);
    hi = B2D(detail::kUcrtAtan2Hi[i]);
    double c = (double)(int64_t)(uint32_t)k * A_1_256;

    // Scale both operands toward 1 in two halves so neither step can overflow.
    int32_t s = 0x3ff - (int32_t)((D2B(b) >> 52) & 0x7ff);
    int32_t s1 = s / 2;
    int32_t s2 = s - s1;
    double f1 = B2D((uint64_t)(int64_t)(s1 + 0x3ff) << 52);
    double f2 = B2D((uint64_t)(int64_t)(s2 + 0x3ff) << 52);
    double den = (f1 * b) * f2;
    double num = (f1 * a) * f2;

    double dhi = B2D(D2B(den) & 0xfffffffff8000000ULL);
    double t = num;
    t = fma(-dhi, c, t);
    t = fma(-c, den - dhi, t);
    double u = fma(num, c, den);
    double q = t / u;                       // (r - c) / (1 + r*c)
    lo = q + B2D(detail::kUcrtAtan2Lo[i]);
    double q2 = q * q;
    double p = fma(-A_P1, q2, A_P2);
    p = p * q2;
    lo = fma(-q, p, lo);
  }

  if (swapped) {
    hi = A_PIO2H - hi;
    lo = A_PIO2L - lo;
  }
  if (xsign) {
    hi = A_PIH - hi;
    lo = A_PIL - lo;
  }
  double r = hi + lo;
  return ysign ? B2D(D2B(r) ^ SIGNMASK) : r;
}

/* ---- host-libm fallbacks, so callers can stay branch-free ---------------- */

inline double UcrtOrHostSin(double aX) {
  return UcrtMathEnabled() ? UcrtSin(aX) : sin(aX);
}
inline double UcrtOrHostCos(double aX) {
  return UcrtMathEnabled() ? UcrtCos(aX) : cos(aX);
}
inline double UcrtOrHostTan(double aX) {
  return UcrtMathEnabled() ? UcrtTan(aX) : tan(aX);
}
inline double UcrtOrHostAtan2(double aY, double aX) {
  return UcrtMathEnabled() ? UcrtAtan2(aY, aX) : atan2(aY, aX);
}

}  // namespace camouwolf

#endif  // BASE_CAMOUWOLF_CAMOUWOLF_UCRT_MATH_H_
