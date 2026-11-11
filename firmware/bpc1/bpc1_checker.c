// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// BPC1 v1 checker: proves that a file arrived bit-perfect, from
// docs/bpc1.md section 12.6. The reference is checker/verify.py.

#include <string.h>

#include "bpc1_checker.h"

#if BPC1_MAX_CH != 2
#error "the inner loops are written for stereo"
#endif

#define RUN_EVERY  256u  // frames received between comparison passes
#define LAG_FRAMES 16u   // the comparison trails the newest frame by this many
#define RMASK      (BPC1_RING_FRAMES - 1u)
#define CHIP_R5    (13u + 5u * 32u)  // the first chip that carries the count (r5)
#define PCM_PLUS   0x20000000u       // W of a +1 chip, and of a -1 chip
#define PCM_MINUS  0xE0000000u
#define DSD_PLUS   0xEEu
#define DSD_MINUS  0x11u
#define DSD_IDLE   0x69u
#define MK_FLIP    0xFF000000u       // 0x05 << 24 to 0xFA << 24 and back
#define MK_MASK    0xFF0000FFu       // a DoP word's marker and padding

#if defined(__GNUC__)
#define RARE(x)  __builtin_expect(!!(x), 0)
#define NOINLINE __attribute__((noinline))
#else
#define RARE(x) (x)
#define NOINLINE
#endif

enum { MODE_PCM, MODE_DOP };
// LEAD: before the body; BODY: comparing it; WAIT: the X-sync after the
// body's last block was neither body nor tail, the next X-sync decides;
// AFTER: the body is done or failed, only the file's end is looked for.
enum { ST_LEAD, ST_BODY, ST_WAIT, ST_AFTER };
enum { SEG_GEN, SEG_PAY, SEG_CONST, SEG_FAIL };

// 8 hash bits -> the 16 DSD bits of one DoP frame: each bit x as (x, not x)
static uint16_t pairs16[256];

// bpc1_lb32() inlined for the inner loops
static inline uint32_t lb32(uint32_t key, uint32_t n) {
  uint32_t x = n ^ key;
  x ^= x >> 16;
  x *= 0x7FEB352Du;
  x ^= x >> 15;
  x *= 0x846CA68Bu;
  x ^= x >> 16;
  return x;
}

//--------------------------------------------------------------------+
// Small helpers
//--------------------------------------------------------------------+

static inline uint64_t min64(uint64_t a, uint64_t b) {
  return a < b ? a : b;
}

static inline uint64_t max64(uint64_t a, uint64_t b) {
  return a > b ? a : b;
}

static inline uint32_t ctz32(uint32_t x) {
#if defined(__GNUC__)
  return x ? (uint32_t) __builtin_ctz(x) : 32u;
#else
  uint32_t n = 0;
  if (x == 0) {
    return 32;
  }
  for (; !(x & 1u); x >>= 1) {
    n++;
  }
  return n;
#endif
}

// Bits from the MSB down to the lowest bit set (0 if none)
static uint8_t effective_bits(uint32_t acc) {
  return (uint8_t) (acc ? 32u - ctz32(acc) : 0u);
}

static inline uint64_t in_units(const bpc1_checker_t *ck) {
  return (ck->in_frames << 1) >> ck->shift;
}

static inline uint32_t rx_unit(const bpc1_checker_t *ck, uint64_t u, uint32_t c) {
  if (!ck->dsd_f) {
    return ck->ring[(uint32_t) u & RMASK][c];
  }
  uint32_t w = ck->ring[(uint32_t) (u >> 1) & RMASK][c];
  return (w >> ((u & 1u) ? 8u : 16u)) & 0xFFu;
}

static bool same_file(const bpc1_checker_t *ck, const bpc1_anchor_t *a) {
  return a->mode == ck->mode && bpc1_same_file(&a->f, &ck->id);
}

// Units per block of an X-sync's file, in its transport (PCM frames, DoP
// bytes), and received frames per block.
static uint32_t block_units(const bpc1_fields_t *f, uint8_t mode, uint32_t *frames) {
  uint32_t rate = bpc1_rate(f->rate_code);
  uint32_t mult = rate / ((rate % 44100u == 0) ? 44100u : 48000u);
  uint32_t u = bpc1_kind_dsd(f->kind) ? 4096u * mult / 8u : 4096u * mult;
  *frames = mode == MODE_DOP ? u / 2u : u;
  return u;
}

// The marker word a DoP body frame f must have
static inline uint32_t mk_of(const bpc1_checker_t *ck, uint64_t f) {
  return (f & 1u) ? ck->mk_even ^ MK_FLIP : ck->mk_even;
}

//--------------------------------------------------------------------+
// The bits in use over the body
//--------------------------------------------------------------------+

// From received unit `from` on, by received block (checker/verify.py takes
// the received units from the body's start for the body's length)
static void or_reset(bpc1_checker_t *ck, uint64_t from) {
  ck->or_on = true;
  ck->or_from = from;
  ck->or_j = 0;
  ck->or_cur = ck->or_prev = 0;
}

static void or_goto(bpc1_checker_t *ck, uint32_t j) {
  if (j > ck->or_j + 8u) {
    ck->or_j = j - 8u;  // what lies further back is not asked for
  }
  while (ck->or_j < j) {
    ck->or_prev |= ck->or_cur;
    ck->or_hist[ck->or_j & 7u] = ck->or_prev;
    ck->or_cur = 0;
    ck->or_j++;
  }
}

// The received blocks of the body, if its last block is known
static bool or_blocks(const bpc1_checker_t *ck, uint32_t *n) {
  if (!ck->E_known || ck->E < ck->L) {
    return false;
  }
  *n = ck->E - ck->L + 1u;
  return true;
}

static uint64_t extent_end(const bpc1_checker_t *ck);

// The words of units u0 to u1, outside the comparison, up to the end of the
// file's extent (checker/verify.py: a body whose end is not known runs to the
// file's stop)
static void or_range(bpc1_checker_t *ck, uint64_t u0, uint64_t u1) {
  u1 = min64(u1, (extent_end(ck) << 1) >> ck->shift);
  if (!ck->or_on || u1 <= ck->or_from) {
    return;
  }
  uint32_t nb = 0;
  bool bounded = or_blocks(ck, &nb);
  u0 = max64(u0, ck->or_from);
  while (u0 < u1) {
    uint32_t j = (uint32_t) ((u0 - ck->or_from) / ck->U);
    if (bounded && j >= nb) {
      return;
    }
    uint64_t e = min64(u1, ck->or_from + (uint64_t) (j + 1u) * ck->U);
    or_goto(ck, j);
    uint64_t f0 = ck->dsd_f ? u0 >> 1 : u0, f1 = ck->dsd_f ? (e + 1u) >> 1 : e;
    uint32_t o = 0;
    for (uint64_t f = f0; f < f1; f++) {
      const uint32_t *w = ck->ring[(uint32_t) f & RMASK];
      o |= w[0] | w[1];
    }
    ck->or_cur |= o;
    u0 = e;
  }
}

static uint8_t or_bits(const bpc1_checker_t *ck) {
  if (!ck->or_on) {
    return 0;
  }
  uint32_t nb, acc = ck->or_prev | ck->or_cur;
  if (or_blocks(ck, &nb) && nb - 1u < ck->or_j) {
    uint32_t j = nb - 1u;
    acc = ck->or_hist[(ck->or_j - j <= 8u ? j : ck->or_j - 8u) & 7u];
  }
  return effective_bits(acc);
}

//--------------------------------------------------------------------+
// Differences
//--------------------------------------------------------------------+

// The file is NOT_BIT_PERFECT from source unit s on; its comparison stops.
static void fail(bpc1_checker_t *ck, uint64_t s, uint8_t why) {
  if (!ck->failed) {
    ck->failed = true;
    ck->diff_s = s;
    ck->diff_rx = ck->placed ? (uint64_t) ((int64_t) s - ck->base) : ck->p;
    ck->why = why;
    if (ck->state == ST_BODY && ck->blk_valid && bpc1_is_rhdr(ck->blk.k)) {
      ck->rec_fail = ck->rec_cur;
      ck->have_fail = true;
    }
  }
  ck->state = ST_AFTER;
}

static uint32_t rhdr_start(const bpc1_checker_t *ck) {
  return ck->dsd_f ? ck->sig.r_start / 8u : ck->sig.r_start;
}

static uint32_t chip_units(const bpc1_checker_t *ck) {
  return ck->dsd_f ? ck->sig.chip / 8u : ck->sig.chip;
}

// Source unit of chip q of R-header block k
static uint64_t chip_at(const bpc1_checker_t *ck, uint32_t k, uint32_t q) {
  return (uint64_t) k * ck->U + rhdr_start(ck) + (uint64_t) q * chip_units(ck);
}

// Where the recorded r5 differs from the count T: the first r5 chip, as a
// source unit; UINT64_MAX if it does not, or its words were not judged.
static uint64_t rec_differs(const bpc1_checker_t *ck, const bpc1_rrec_t *r, uint16_t T) {
  if (!r->ok) {
    return UINT64_MAX;
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    if ((uint16_t) (r->w[c] >> 16) != T) {
      return chip_at(ck, r->k, CHIP_R5);
    }
  }
  return UINT64_MAX;
}

// The words of the file's first R-header are judged: in the lead-in, not
// compared, they passed section 6 where they were read; in the body, its
// chips arrived whole without a difference and its CRC passed; in the tail,
// or where the body's start is not known, never.
static bool count_judged(const bpc1_checker_t *ck) {
  if (!ck->placed) {
    return false;
  }
  if (ck->rcount_k < ck->L) {
    return true;
  }
  return ck->have_first && ck->rec_first.k == ck->rcount_k && ck->rec_first.ok;
}

// The file's first R-header settles the R-headers' r5 (12.6, the R-header
// rule). Its count, or without one the LAST block's index + 1, is the
// file's size: where the body's first R-header or the one that failed
// differs from it is a difference. Where that count disagrees with the
// file, an intact LAST X-sync whose index + 1 is another or an X-sync of
// the file at a block index no lower than it, the first R-header's first
// r5 unit is a difference.
static void settle(bpc1_checker_t *ck) {
  uint64_t d = UINT64_MAX;
  uint32_t T = ck->rcount_known ? ck->rcount : ck->last_known ? ck->total : 0u;
  if (T && ck->have_first) {
    d = rec_differs(ck, &ck->rec_first, (uint16_t) T);
    if (ck->have_fail) {
      d = min64(d, rec_differs(ck, &ck->rec_fail, (uint16_t) T));
    }
  }
  bool other = (ck->last_known && ck->rcount != ck->total) || ck->rcount <= ck->last_k;
  if (ck->rcount_known && other && count_judged(ck)) {
    d = min64(d, chip_at(ck, ck->rcount_k, CHIP_R5));
  }
  if (d == UINT64_MAX) {
    return;
  }
  if (ck->failed && ck->why != BPC1_WHY_COUNT && d >= ck->diff_s) {
    return;  // an earlier difference stands
  }
  // with the count known, the R-header that differs from it
  ck->failed = true;
  ck->why = BPC1_WHY_COUNT;
  ck->diff_s = d;
  ck->diff_rx = (uint64_t) ((int64_t) d - ck->base);
}

//--------------------------------------------------------------------+
// Comparison of the body
//--------------------------------------------------------------------+

// Units from u on (block offset i), up to n, against block b by the
// reference functions of bpc1.c, or against the constants cv; DoP frames
// with their markers. Returns the units equal; ORs their words into orv.
static uint32_t gen_equal(const bpc1_checker_t *ck, const bpc1_block_t *b, uint64_t u, uint32_t i, uint32_t n,
                          const uint32_t *cv, uint32_t *orv) {
  uint32_t t = 0, o = 0;
  for (; t < n; t++) {
    uint64_t x = u + t;
    bool eq = true;
    for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
      uint32_t e = cv ? cv[c] : bpc1_unit(&ck->sig, b, c, i + t);
      eq = eq && rx_unit(ck, x, c) == e;
    }
    const uint32_t *w = ck->ring[(uint32_t) (ck->dsd_f ? x >> 1 : x) & RMASK];
    if (ck->dsd_f) {
      uint32_t mk = mk_of(ck, x >> 1);
      eq = eq && (w[0] & MK_MASK) == mk && (w[1] & MK_MASK) == mk;
    }
    if (!eq) {
      break;
    }
    o |= w[0] | w[1];
  }
  *orv |= o;
  return t;
}

// PCM payload frames from received frame p on, up to n, while each equals the
// expected one, source frame n0 on: the hash shifted to the active bits (as)
// and left-justified from the depth (sh). Returns the frames done; ORs their
// samples into orv. On its own, so that the loop keeps its values in registers.
NOINLINE static uint32_t pcm_payload_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t p, uint32_t n,
                                           uint32_t key0, uint32_t key1, uint32_t n0, uint32_t as, uint32_t sh,
                                           uint32_t *orv) {
  uint32_t t = 0, o = 0;
  for (; t < n; t++) {
    uint32_t e0 = (uint32_t) ((int32_t) lb32(key0, n0 + t) >> as) << sh;
    uint32_t e1 = (uint32_t) ((int32_t) lb32(key1, n0 + t) >> as) << sh;
    const uint32_t *r = ring[(p + t) & RMASK];
    if ((r[0] ^ e0) | (r[1] ^ e1)) {
      break;
    }
    o |= r[0] | r[1];
  }
  *orv |= o;
  return t;
}

// SWEEP payload frames from received frame p on, up to n, while each equals
// the expected one, sweep index s0 on: the code lbD(D, key, s) left-justified.
// One loop per depth, so that D, its masks and shifts are constants.
static inline uint32_t sweep_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t p, uint32_t n, uint32_t key0,
                                   uint32_t key1, uint32_t s0, uint32_t depth, uint32_t *orv) {
  uint32_t t = 0, o = 0, sh = 32u - depth;
  for (; t < n; t++) {
    uint32_t e0 = bpc1_lbd(depth, key0, s0 + t) << sh;
    uint32_t e1 = bpc1_lbd(depth, key1, s0 + t) << sh;
    const uint32_t *r = ring[(p + t) & RMASK];
    if ((r[0] ^ e0) | (r[1] ^ e1)) {
      break;
    }
    o |= r[0] | r[1];
  }
  *orv |= o;
  return t;
}

NOINLINE static uint32_t sweep16_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t p, uint32_t n, uint32_t key0,
                                       uint32_t key1, uint32_t s0, uint32_t *orv) {
  return sweep_equal(ring, p, n, key0, key1, s0, 16, orv);
}

NOINLINE static uint32_t sweep24_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t p, uint32_t n, uint32_t key0,
                                       uint32_t key1, uint32_t s0, uint32_t *orv) {
  return sweep_equal(ring, p, n, key0, key1, s0, 24, orv);
}

static uint32_t pcm_const_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t p, uint32_t n, uint32_t e0,
                                uint32_t e1, uint32_t *orv) {
  uint32_t t = 0, o = 0;
  for (; t < n; t++) {
    const uint32_t *r = ring[(p + t) & RMASK];
    if ((r[0] ^ e0) | (r[1] ^ e1)) {
      break;
    }
    o |= r[0] | r[1];
  }
  *orv |= o;
  return t;
}

// One DoP frame against the marker mk and the expected 16 bits per channel,
// zero bits below them: 0 if equal
static inline uint32_t dop_frame_x(const uint32_t r[], uint32_t h0, uint32_t h1, uint32_t sh, uint32_t mk) {
  return (r[0] ^ (mk | ((uint32_t) pairs16[(h0 >> sh) & 0xFFu] << 8)))
         | (r[1] ^ (mk | ((uint32_t) pairs16[(h1 >> sh) & 0xFFu] << 8)));
}

// DoP payload frames from received frame f0 on, up to nf, while each equals
// the expected one. q: the hash of the first frame (64 DSD bits, 4 frames),
// j: the frame within it, mk: the first frame's marker. Returns the frames
// done; ORs their words into orv. On its own, so that the loop keeps its
// values in registers.
NOINLINE static uint32_t dop_payload_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t f0, uint32_t nf,
                                           uint32_t key0, uint32_t key1, uint32_t q, uint32_t j, uint32_t mk,
                                           uint32_t *orv) {
  uint32_t fr = 0, o = 0;
  while (fr < nf) {
    uint32_t h0 = lb32(key0, q), h1 = lb32(key1, q);
    if (j == 0 && nf - fr >= 4u) {
      // the four frames of one hash
      const uint32_t *r0 = ring[(f0 + fr) & RMASK], *r1 = ring[(f0 + fr + 1u) & RMASK];
      const uint32_t *r2 = ring[(f0 + fr + 2u) & RMASK], *r3 = ring[(f0 + fr + 3u) & RMASK];
      uint32_t m1 = mk ^ MK_FLIP;
      uint32_t x = dop_frame_x(r0, h0, h1, 24, mk) | dop_frame_x(r1, h0, h1, 16, m1)
                   | dop_frame_x(r2, h0, h1, 8, mk) | dop_frame_x(r3, h0, h1, 0, m1);
      if (x == 0) {
        o |= r0[0] | r0[1] | r1[0] | r1[1] | r2[0] | r2[1] | r3[0] | r3[1];
        fr += 4u;
        q++;
        continue;
      }
    }
    // frame by frame up to the end of the hash, or the first that differs
    for (; j < 4u && fr < nf; j++, fr++) {
      const uint32_t *r = ring[(f0 + fr) & RMASK];
      if (dop_frame_x(r, h0, h1, 24u - 8u * j, mk)) {
        *orv |= o;
        return fr;
      }
      o |= r[0] | r[1];
      mk ^= MK_FLIP;
    }
    j = 0;
    q++;
  }
  *orv |= o;
  return fr;
}

static uint32_t dop_const_equal(const uint32_t (*ring)[BPC1_MAX_CH], uint32_t f0, uint32_t nf, uint32_t e0,
                                uint32_t e1, uint32_t mk, uint32_t *orv) {
  uint32_t fr = 0, o = 0;
  for (; fr < nf; fr++, mk ^= MK_FLIP) {
    const uint32_t *r = ring[(f0 + fr) & RMASK];
    if ((r[0] ^ (mk | e0)) | (r[1] ^ (mk | e1))) {
      break;
    }
    o |= r[0] | r[1];
  }
  *orv |= o;
  return fr;
}

// n units of segment seg from p (block offset i) compared; returns the units
// equal.
static uint32_t compare(const bpc1_checker_t *ck, uint8_t seg, uint32_t i, uint32_t n, const uint32_t cv[],
                        uint32_t *orv) {
  const bpc1_signal_t *g = &ck->sig;
  const bpc1_block_t *b = &ck->blk;
  uint64_t p = ck->p;
  const uint32_t *cvp = seg == SEG_CONST ? cv : NULL;
  if (!ck->dsd_f) {
    if (seg == SEG_PAY && g->sweep) {
      uint32_t s0 = bpc1_sweep_index(b->k, g->block_len, i);
      return (g->depth == 16 ? sweep16_equal : sweep24_equal)(ck->ring, (uint32_t) p, n, g->sweep_keys[0],
                                                             g->sweep_keys[1], s0, orv);
    }
    if (seg == SEG_PAY) {
      return pcm_payload_equal(ck->ring, (uint32_t) p, n, g->keys[0], g->keys[1], b->k * g->block_len + i,
                               32u - g->active, 32u - g->depth, orv);
    }
    if (seg == SEG_CONST) {
      return pcm_const_equal(ck->ring, (uint32_t) p, n, cv[0], cv[1], orv);
    }
    return gen_equal(ck, b, p, i, n, NULL, orv);
  }
  // DoP: whole frames; X-syncs, and units not starting a frame, one by one
  if (seg == SEG_GEN || ((p | i | n) & 1u)) {
    return gen_equal(ck, b, p, i, n, cvp, orv);
  }
  uint32_t nf = n >> 1, fr;
  uint32_t f0 = (uint32_t) (p >> 1);
  uint32_t mk = mk_of(ck, f0);
  if (seg == SEG_PAY) {
    uint32_t q = (b->k * g->block_len + 8u * i) >> 6;
    fr = dop_payload_equal(ck->ring, f0, nf, g->keys[0], g->keys[1], q, (i >> 1) & 3u, mk, orv);
  } else {
    fr = dop_const_equal(ck->ring, f0, nf, (cv[0] << 16) | (cv[0] << 8), (cv[1] << 16) | (cv[1] << 8), mk, orv);
  }
  return 2u * fr;
}

// The words of the chips the R-header block spelled, every chip whole and
// without a difference: r6 passes the CRC with r0 to r4 of the block and r5
// (section 6), and r5 is the same in every channel and as in the first
// R-header of the body whose CRC passed; the file's size, from its first
// R-header or its LAST block, settles it later (settle). A failure is at the
// block's first r5 unit.
static bool rhdr_crc(bpc1_checker_t *ck) {
  bpc1_rrec_t *r = &ck->rec_cur;
  r->crc_done = true;
  if (ck->rec_is_first) {
    ck->rec_first = *r;
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint16_t w[6];
    memcpy(w, ck->blk.rhdr[c], 5 * sizeof(w[0]));
    w[5] = (uint16_t) (r->w[c] >> 16);
    if (bpc1_crc16_words(w, 6) != (uint16_t) r->w[c]) {
      fail(ck, chip_at(ck, r->k, CHIP_R5), BPC1_WHY_CRC);
      return false;
    }
  }
  r->ok = true;
  if (ck->rec_is_first) {
    ck->rec_first = *r;
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint16_t r5 = (uint16_t) (r->w[c] >> 16);
    if (!ck->r5_known) {
      ck->r5_known = true;
      ck->r5 = r5;
    }
    if (r5 != ck->r5) {
      fail(ck, chip_at(ck, r->k, CHIP_R5), BPC1_WHY_COUNT);
      return false;
    }
  }
  return true;
}

// At the start of the first chip of an r5 or r6 bit: its value in each
// channel, one of the two, gives the bit.
static bool chip_decide(bpc1_checker_t *ck) {
  bpc1_rrec_t *r = &ck->rec_cur;
  uint64_t s = ck->p + (uint64_t) ck->base;
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint32_t v = rx_unit(ck, ck->p, c);
    uint32_t plus = ck->dsd_f ? DSD_PLUS : PCM_PLUS, minus = ck->dsd_f ? DSD_MINUS : PCM_MINUS;
    if (v != plus && v != minus) {
      fail(ck, s, BPC1_WHY_CHIP);
      return false;
    }
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint32_t v = rx_unit(ck, ck->p, c);
    r->w[c] = (r->w[c] << 1) | (v == (ck->dsd_f ? DSD_PLUS : PCM_PLUS));
  }
  r->n++;
  if (ck->rec_is_first) {
    ck->rec_first = *r;
  }
  return true;
}

// The stretch of the block being compared from offset i on with one way of
// computing the expected units: GEN (X-sync, burst), PAY (hash) or CONST
// (cv). In the chips of r5 and r6 the first chip of each bit is taken as
// received, if it is one of its two values; the second must be the opposite.
// FAIL if that or a check of the R-header words failed.
static uint8_t segment(bpc1_checker_t *ck, uint32_t i, uint32_t *end, uint32_t cv[]) {
  bool dsd = ck->dsd_f;
  const bpc1_block_t *b = &ck->blk;
  uint32_t sync = dsd ? BPC1_SYNC_BYTES : BPC1_BURST_END;
  if (i < sync) {
    *end = sync;
    return SEG_GEN;
  }
  if (!bpc1_is_rhdr(b->k)) {
    *end = ck->U;
    return SEG_PAY;
  }
  uint32_t a = rhdr_start(ck), L = chip_units(ck);
  cv[0] = cv[1] = dsd ? DSD_IDLE : 0u;
  if (i < a) {
    *end = a;
    return SEG_CONST;
  }
  uint32_t q = (i - a) / L;
  if (q >= BPC1_N_CHIPS) {
    *end = ck->U;
    if (ck->rec_cur.n == 32 && !ck->rec_cur.crc_done && !rhdr_crc(ck)) {
      return SEG_FAIL;
    }
    return SEG_CONST;
  }
  *end = a + (q + 1u) * L;
  uint32_t plus = dsd ? DSD_PLUS : PCM_PLUS, minus = dsd ? DSD_MINUS : PCM_MINUS;
  if (q < CHIP_R5) {
    for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
      cv[c] = bpc1_chip(b->rhdr[c], q) > 0 ? plus : minus;
    }
    return SEG_CONST;
  }
  uint32_t rel = q - CHIP_R5, bit = rel >> 1;
  if (bit >= ck->rec_cur.n) {
    if ((rel & 1u) || i != a + q * L) {
      fail(ck, ck->p + (uint64_t) ck->base, BPC1_WHY_CHIP);  // not reached: chips are entered at their start
      return SEG_FAIL;
    }
    if (!chip_decide(ck)) {
      return SEG_FAIL;
    }
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    bool one = (ck->rec_cur.w[c] >> (ck->rec_cur.n - 1u - bit)) & 1u;
    cv[c] = (one != (bool) (rel & 1u)) ? plus : minus;
  }
  return SEG_CONST;
}

// The body is done: its limit after it is shown
static void body_complete(bpc1_checker_t *ck) {
  ck->body_done = true;
  ck->state = ST_AFTER;
}

// At the start of block k in the body: its X-sync compared as a body
// block's (flags 0, or LAST), else the body ended before it: at a tail
// X-sync, which the file's X-syncs have shown, or the next one decides.
static void body_sync(bpc1_checker_t *ck, uint32_t k) {
  if (ck->E_known && k > ck->E) {
    body_complete(ck);
    return;
  }
  or_goto(ck, k - ck->L);
  uint32_t n = ck->dsd_f ? BPC1_SYNC_BYTES : BPC1_SYNC_FRAMES, orv = 0;
  n = (uint32_t) min64(n, in_units(ck) - ck->p);
  bpc1_block_init(&ck->sig, k, 0, false, 0, &ck->blk);
  ck->blk_valid = true;
  uint32_t t0 = gen_equal(ck, &ck->blk, ck->p, 0, n, NULL, &orv);
  if (t0 < n) {
    bpc1_block_t last;
    bpc1_block_init(&ck->sig, k, BPC1_F_LAST, false, 0, &last);
    orv = 0;
    uint32_t tl = gen_equal(ck, &last, ck->p, 0, n, NULL, &orv);
    if (tl < n) {
      // Neither: the first unit that differs from the X-sync the block has
      uint32_t t = (ck->last_known && k + 1u == ck->total) ? tl : t0;
      ck->pend_t = t;
      bool body_here = ck->last_u == ck->p && ck->lastx_k == k && !(ck->lastx_fl & BPC1_F_TAIL);
      if (k == ck->L || body_here) {
        fail(ck, (uint64_t) k * ck->U + t, BPC1_WHY_UNIT);  // a body block's X-sync
      } else {
        ck->pend_k = k;
        ck->state = ST_WAIT;
      }
      return;
    }
    ck->blk = last;
    ck->E_known = true;
    ck->E = k;
    if (!ck->last_known) {
      ck->last_known = true;
      ck->total = (uint16_t) (k + 1u);
    }
  }
  if (bpc1_is_rhdr(k)) {
    memset(&ck->rec_cur, 0, sizeof(ck->rec_cur));
    ck->rec_cur.k = k;
    ck->rec_cur.flags = ck->blk.flags;
    ck->rec_is_first = !ck->have_first;
    if (ck->rec_is_first) {
      ck->have_first = true;
      ck->rec_first = ck->rec_cur;
    }
  }
  ck->p += n;
  ck->compared += n;
  ck->or_cur |= orv;
}

// Compares the body from p up to lim, or to its first difference
static void body_run(bpc1_checker_t *ck, uint64_t lim) {
  while (ck->state == ST_BODY && ck->p < lim) {
    uint64_t s = ck->p + (uint64_t) ck->base;
    uint32_t k = (uint32_t) (s / ck->U), i = (uint32_t) (s - (uint64_t) k * ck->U);
    if (!ck->blk_valid || ck->blk.k != k) {
      body_sync(ck, k);
      continue;
    }
    uint32_t end, cv[BPC1_MAX_CH], orv = 0;
    uint8_t seg = segment(ck, i, &end, cv);
    if (seg == SEG_FAIL) {
      return;
    }
    uint32_t n = (uint32_t) min64(end - i, lim - ck->p);
    uint32_t m = compare(ck, seg, i, n, cv, &orv);
    ck->p += m;
    ck->compared += m;
    ck->or_cur |= orv;
    if (RARE(m < n)) {
      uint8_t why = BPC1_WHY_UNIT;
      if (ck->dsd_f) {
        // the unit of the frame that differs; a marker or padding: its first
        const uint32_t *w = ck->ring[(uint32_t) (ck->p >> 1) & RMASK];
        uint32_t mk = mk_of(ck, ck->p >> 1);
        if ((w[0] & MK_MASK) != mk || (w[1] & MK_MASK) != mk) {
          why = BPC1_WHY_MARKER;
        } else if (seg != SEG_GEN) {
          ck->p += gen_equal(ck, &ck->blk, ck->p, i + m, 1, seg == SEG_CONST ? cv : NULL, &orv);
        }
      }
      fail(ck, ck->p + (uint64_t) ck->base, why);
    }
  }
}

//--------------------------------------------------------------------+
// Files: their X-syncs, start, end and extent
//--------------------------------------------------------------------+

static void report_init(const bpc1_checker_t *ck, bpc1_report_t *r) {
  memset(r, 0, sizeof(*r));
  r->verdict = BPC1_UNKNOWN_SIGNAL;
  r->end = BPC1_END_STOP;
  r->slot_bits = ck->slot_bits;
  r->rx_rate = ck->host_rate;
  r->host_rate = ck->host_rate;
  r->body_k = -1;
}

static void push_report(bpc1_checker_t *ck, const bpc1_report_t *r) {
  if (ck->nout == BPC1_MAX_REPORTS) {
    ck->out_head = (uint8_t) ((ck->out_head + 1u) % BPC1_MAX_REPORTS);
    ck->nout--;
    ck->out_lost++;
  }
  ck->out[(ck->out_head + ck->nout) % BPC1_MAX_REPORTS] = *r;
  ck->nout++;
}

static void pop_anchor(bpc1_checker_t *ck) {
  memmove(&ck->q[0], &ck->q[1], (size_t) (ck->nq - 1u) * sizeof(ck->q[0]));
  ck->nq--;
}

static void pop_overrun(bpc1_checker_t *ck) {
  memmove(&ck->over[0], &ck->over[1], (size_t) (ck->nover - 1u) * sizeof(ck->over[0]));
  ck->nover--;
}

// Silence: PCM words 0, or a DoP frame with a valid marker, the same in
// both channels, DSD idle bytes 0x69 or 0x96 and zero bits below them
static inline bool silent_frame(const bpc1_checker_t *ck, const uint32_t *w) {
  if ((w[0] | w[1]) == 0) {
    return true;
  }
  uint32_t m = w[0] >> 24;
  if (ck->slot_bits < 24 || (m != 0x05u && m != 0xFAu) || (w[1] >> 24) != m) {
    return false;
  }
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint32_t b1 = (w[c] >> 16) & 0xFFu, b2 = (w[c] >> 8) & 0xFFu;
    if ((b1 != 0x69u && b1 != 0x96u) || (b2 != 0x69u && b2 != 0x96u) || (w[c] & 0xFFu)) {
      return false;
    }
  }
  return true;
}

// Frames from `from` on are outside the extents known so far: watch them.
// Those already looked at again where the first frame not silent no longer
// counts.
static void sil_watch(bpc1_checker_t *ck, uint64_t from) {
  if (from == ck->sil_from) {
    return;
  }
  if (from < ck->sil_from || ck->sil_ns < from) {
    uint64_t oldest = ck->in_frames > BPC1_RING_FRAMES ? ck->in_frames - BPC1_RING_FRAMES : 0;
    ck->sil_ns = UINT64_MAX;
    ck->sil_scan = max64(from, oldest);
  }
  ck->sil_from = from;
  ck->sil_scan = max64(ck->sil_scan, from);
}

// Looks at the frames received since, up to the first not silent: only
// frames outside the extents known, so none while a file's X-syncs arrive.
static void sil_look(bpc1_checker_t *ck) {
  if (!ck->allow_silence || ck->sil_ns != UINT64_MAX) {
    return;
  }
  uint64_t f = ck->sil_scan;
  for (; f < ck->in_frames; f++) {
    if (!silent_frame(ck, ck->ring[(uint32_t) f & RMASK])) {
      ck->sil_ns = f;
      break;
    }
  }
  ck->sil_scan = f;
}

// Frames from..to of the stream are outside every file
// A stretch of unknown signal; false if it was allowed silence, no result
static bool unknown_stretch(bpc1_checker_t *ck, uint64_t from, uint64_t to, bool overrun) {
  if (ck->allow_silence && !overrun) {
    sil_watch(ck, from);
    sil_look(ck);
    if (ck->sil_ns >= to) {
      ck->outside += to - from;
      ck->silent += to - from;  // silence, allowed
      return false;
    }
  }
  bpc1_report_t r;
  report_init(ck, &r);
  r.verdict = overrun ? BPC1_OVERRUN : BPC1_UNKNOWN_SIGNAL;
  r.first_frame = from;
  r.frames = to - from;
  ck->outside += to - from;
  push_report(ck, &r);
  return true;
}

// The first X-sync after the lead-in: the body starts there if it is that
// of the block after the last lead-in X-sync, or of block 0.
static void place_body(bpc1_checker_t *ck, const bpc1_anchor_t *a, uint64_t au) {
  uint32_t k = a->f.block;
  if (!ck->lead_seen && k != 0) {
    fail(ck, (uint64_t) k * ck->U, BPC1_WHY_START);  // the start of the body is not shown
    ck->or_on = false;
    return;
  }
  ck->L = ck->lead_seen ? ck->lead_k + 1u : 0u;
  ck->placed = true;
  uint64_t s0 = (uint64_t) ck->L * ck->U;
  ck->body_u = k == ck->L ? au : ck->lead_u + ck->U;  // else where it should have been
  ck->base = (int64_t) s0 - (int64_t) ck->body_u;
  if (k == ck->L) {
    or_reset(ck, au);
  } else {
    fail(ck, s0, BPC1_WHY_FIRST);  // the OR runs from where the body should have been
  }
  if (!ck->rate_ok) {
    fail(ck, s0, BPC1_WHY_RATE);
    ck->or_on = false;
    return;
  }
  if (ck->failed) {
    return;
  }
  if (a->flags & BPC1_F_TAIL) {
    fail(ck, s0, BPC1_WHY_START);  // no body between the lead-in and the tail
    return;
  }
  if (ck->dsd_f) {
    uint32_t m = (ck->ring[(uint32_t) (au >> 1) & RMASK][0] & 0xFF000000u);
    if (m != 0x05000000u && m != 0xFA000000u) {
      m = 0x05000000u;  // the frame fails
    }
    ck->mk_even = ((au >> 1) & 1u) ? m ^ MK_FLIP : m;
  }
  ck->blk_valid = false;
  ck->state = ST_BODY;
}

// An X-sync of the file being checked
static void file_anchor(bpc1_checker_t *ck, const bpc1_anchor_t *a) {
  const bpc1_fields_t *f = &a->f;
  uint8_t fl = a->flags;
  uint64_t au = (a->h + (1u << ck->shift) - 1u) >> ck->shift;
  bool first = ck->anchors == 0;
  ck->anchors++;
  if (first || f->block > ck->last_k) {
    ck->last_k = f->block;
  }
  ck->lastx_k = f->block;
  ck->lastx_fl = fl;
  ck->lastx_f = a->h >> 1;
  ck->last_u = au;
  ck->last_last = (fl & BPC1_F_LAST) != 0;
  if (fl & BPC1_F_LAST) {
    if (!ck->last_known) {
      ck->last_known = true;
      ck->total = (uint16_t) (f->block + 1u);
    }
    ck->end_known = true;
    ck->end_u = au + ck->U;
  }
  if ((fl & BPC1_F_TAIL) && !ck->tail_seen) {
    ck->tail_seen = true;
    ck->E_known = true;
    ck->E = f->block - 1u;
  } else if ((fl & BPC1_F_LAST) && !ck->tail_seen) {
    ck->E_known = true;
    ck->E = f->block;
  }
  switch (ck->state) {
    case ST_LEAD:
      if (fl & BPC1_F_LEAD_IN) {
        if (!ck->lead_seen || f->block > ck->lead_k) {
          ck->lead_seen = true;
          ck->lead_k = f->block;
          ck->lead_u = au;
          ck->L = f->block + 1u;
          or_reset(ck, au + ck->U);  // the body's place if its own X-sync is not seen
        }
      } else {
        place_body(ck, a, au);
      }
      break;
    case ST_WAIT:
      if (f->block < ck->pend_k) {
        break;  // a block of the body again, after it: the next X-sync decides
      }
      if ((fl & BPC1_F_TAIL) && f->block == ck->pend_k) {
        body_complete(ck);  // a tail X-sync of the block after the body, later
      } else {
        fail(ck, (uint64_t) ck->pend_k * ck->U + ck->pend_t, BPC1_WHY_UNIT);  // it was a body block
      }
      break;
    default:
      break;
  }
}

// The block count the file's extent ends by (12.6 Extent): its first
// R-header's, if greater than its latest X-sync's block index (a smaller one
// is a difference, settle()), else its LAST block's index + 1; 0 if neither
static uint32_t extent_count(const bpc1_checker_t *ck) {
  if (ck->rcount_known && ck->rcount > ck->lastx_k) {
    return ck->rcount;
  }
  return ck->last_known ? ck->total : 0u;
}

// Where the file's extent ends: the end of its last block plus one block, as
// its latest X-sync implies
static uint64_t extent_end(const bpc1_checker_t *ck) {
  uint32_t uf;
  (void) block_units(&ck->id, ck->mode, &uf);
  uint32_t count = extent_count(ck);
  uint64_t blocks = count > ck->lastx_k ? count - ck->lastx_k : 1u;
  return ck->lastx_f + (blocks + 1u) * uf;
}

// The file completes at its LAST block's end, if its units reach it before
// the next file's first X-sync and the stream's end: its LAST X-sync
// arrived, and no R-header gives it another count (12.6 Files in a stream)
static bool size_ok(const bpc1_checker_t *ck) {
  return ck->end_known && (!ck->rcount_known || ck->rcount == ck->total);
}

// An overrun stands before the unit at its place, and belongs to the
// result that holds that unit (12.6). The file being checked surely holds
// the units before this, in half-frames: before its LAST block's end where
// it can complete there, else up to its extent's end, which a cut file
// holds with an overrun there. Where the next file's first X-sync is,
// cursor_step() leaves the overrun to finish_file(). An overrun at or after
// it stays queued until a later X-sync or the file's end decides.
static uint64_t held_end(const bpc1_checker_t *ck) {
  if (size_ok(ck)) {
    return min64(ck->end_u << ck->shift, extent_end(ck) << 1);
  }
  return (extent_end(ck) << 1) + 1u;
}

// The overruns before half-frame end_h are the file's
static void take_overruns(bpc1_checker_t *ck, uint64_t end_h) {
  while (ck->nover && ck->over[0] < end_h) {
    if (!ck->overrun) {
      ck->over_rx = (ck->over[0] + (1u << ck->shift) - 1u) >> ck->shift;
    }
    ck->overrun = true;
    pop_overrun(ck);
  }
}

// The report of the file, its units ending at unit stop
static void fill_report(const bpc1_checker_t *ck, uint8_t end, uint64_t stop, bpc1_report_t *r) {
  const bpc1_signal_t *g = &ck->sig;
  report_init(ck, r);
  // A difference before the first overrun is judged; an overrun before it is not
  bool judged = ck->failed && (!ck->overrun || ck->diff_rx < ck->over_rx);
  r->verdict = judged ? BPC1_NOT_BIT_PERFECT : ck->overrun ? BPC1_OVERRUN : BPC1_BIT_PERFECT;
  r->end = end;
  r->kind = ck->id.kind;
  r->dop = ck->dsd_f;
  r->id = ck->id;
  r->rate = g->rate;
  r->rx_rate = ck->host_rate * (ck->dsd_f ? 16u : 1u);
  r->depth = g->depth;
  r->bits = or_bits(ck);
  r->why = ck->why;
  if (r->verdict == BPC1_NOT_BIT_PERFECT) {
    // source unit / file rate; DSD units are bytes, 8 bits each
    uint64_t per = (uint64_t) g->rate / (g->dsd ? 8u : 1u);
    r->diff_known = true;
    r->diff_unit = ck->diff_s;
    r->diff_ms = (uint32_t) ((ck->diff_s * 1000u + per / 2u) / per);
  }
  r->body_k = ck->placed ? (int32_t) ck->L : -1;
  r->compared = ck->compared;
  r->first_frame = ck->first_h >> 1;
  // a finished file's frames end where its units do, at most with its
  // extent, where unknown signal starts
  uint64_t to = ((stop << ck->shift) + 1u) >> 1;
  r->frames = (end == BPC1_END_OPEN ? to : min64(to, extent_end(ck))) - r->first_frame;
  r->anchors = ck->anchors;
}

// The R-header decoded by half-frame `upto`: the file's first R-header read
// gives its count (12.6 Extent)
static void take_rcount(bpc1_checker_t *ck, uint64_t upto) {
  if (!ck->rdec.done || ck->rdec.seen > upto) {
    return;
  }
  ck->rdec.done = false;
  if (!ck->rcount_known && ck->rdec.mode == ck->mode && bpc1_same_file(&ck->rdec.f, &ck->id) &&
      ck->rdec.h >= ck->first_h) {
    ck->rcount_known = true;
    ck->rcount = ck->rdec.total;
    ck->rcount_k = ck->rdec.f.block;
  }
}

// The file ends (end); the next file's first X-sync, or the stream's end, is
// at unit au. Complete, its units end at its LAST block's end, and an
// overrun there belongs to what follows; cut, they end at au, at most at its
// extent's end, and an overrun there is its own (12.6 verdict 2).
static void finish_file(bpc1_checker_t *ck, uint8_t end, uint64_t au) {
  take_rcount(ck, au << ck->shift);  // decoded by the file's end, not yet reached by a step
  bool complete = size_ok(ck) && ck->end_u <= au;
  uint64_t stop = complete ? ck->end_u : min64(au, (extent_end(ck) << 1) >> ck->shift);
  take_overruns(ck, (stop << ck->shift) + (complete ? 0u : 1u));
  if (ck->state == ST_BODY && ck->E_known && ck->p + (uint64_t) ck->base >= (uint64_t) (ck->E + 1u) * ck->U) {
    body_complete(ck);
  }
  if (!ck->failed && ck->state == ST_BODY && ck->blk_valid && bpc1_is_rhdr(ck->blk.k) && ck->rec_cur.n == 32 &&
      !ck->rec_cur.crc_done && ck->p + (uint64_t) ck->base >= chip_at(ck, ck->blk.k, BPC1_N_CHIPS)) {
    (void) rhdr_crc(ck);  // the stream stopped right after the chips, which arrived whole
  }
  if (!ck->failed) {
    switch (ck->state) {
      case ST_LEAD:  // no X-sync after the lead-in
        ck->L = ck->lead_k + 1u;
        ck->placed = true;
        ck->body_u = ck->lead_u + ck->U;
        ck->base = (int64_t) ck->L * ck->U - (int64_t) ck->body_u;
        fail(ck, (uint64_t) ck->L * ck->U, ck->rate_ok ? BPC1_WHY_FIRST : BPC1_WHY_RATE);
        if (!ck->rate_ok) {
          ck->or_on = false;
        }
        break;
      case ST_BODY:  // the body was left: the first unit that did not arrive
        fail(ck, max64(min64(ck->p, stop), ck->body_u) + (uint64_t) ck->base, BPC1_WHY_LEFT);
        break;
      case ST_WAIT:
        fail(ck, (uint64_t) ck->pend_k * ck->U + ck->pend_t, BPC1_WHY_END);
        break;
      default:
        break;
    }
  }
  if (ck->state == ST_LEAD || ck->state == ST_WAIT || ck->state == ST_BODY) {
    ck->state = ST_AFTER;
  }
  settle(ck);
  bpc1_report_t r;
  fill_report(ck, end, stop, &r);
  r.cut = !complete;
  push_report(ck, &r);
  ck->cov_end = max64(ck->cov_end, extent_end(ck));
  ck->fin_complete = complete;
  ck->active = false;
}

// Opens the file of the X-sync a; the stretch between the extents before it
// and its own is unknown signal.
static void open_file(bpc1_checker_t *ck, const bpc1_anchor_t *a) {
  uint32_t uf;
  (void) block_units(&a->f, a->mode, &uf);
  uint64_t f1 = a->h >> 1, back = (uint64_t) (a->f.block + 1u) * uf;
  uint64_t S = f1 > back ? f1 - back : 0;
  // the overruns still queued before the X-sync: the stretch's before the
  // extent's start, the file's from there on
  bool stretch = ck->cov_end < S, over_stretch = false, over_file = false;
  while (ck->nover && ck->over[0] < a->h) {
    if (stretch && ck->over[0] < 2u * S) {
      over_stretch = true;
    } else {
      over_file = true;
    }
    pop_overrun(ck);
  }
  if (stretch) {
    unknown_stretch(ck, ck->cov_end, S, over_stretch);
    ck->cov_end = S;
  }

  // The file's state
  bpc1_fields_t f = a->f;
  if (f.nch > BPC1_MAX_CH) {
    f.nch = BPC1_MAX_CH;  // keys for the channels received; rate_ok fails it
  }
  ck->active = true;
  ck->mode = a->mode;
  ck->dsd_f = a->mode == MODE_DOP;
  ck->shift = ck->dsd_f ? 0 : 1;
  ck->state = ST_LEAD;
  ck->id = a->f;
  bpc1_signal_init(&ck->sig, &f);
  ck->U = ck->sig.units;
  ck->rate_ok = a->f.nch == BPC1_MAX_CH && ck->host_rate * (ck->dsd_f ? 16u : 1u) == ck->sig.rate;
  ck->p = (a->h + (1u << ck->shift) - 1u) >> ck->shift;
  ck->first_h = a->h;
  ck->anchors = 0;
  ck->last_k = ck->lastx_k = 0;
  ck->lastx_f = ck->last_u = 0;
  ck->last_last = ck->lead_seen = ck->end_known = ck->last_known = ck->rcount_known = false;
  ck->tail_seen = ck->E_known = ck->placed = ck->blk_valid = ck->r5_known = false;
  ck->have_first = ck->have_fail = ck->rec_is_first = ck->body_done = ck->failed = false;
  ck->compared = 0;
  ck->why = BPC1_WHY_NONE;
  ck->overrun = over_file;
  ck->over_rx = 0;  // before the file's first X-sync
  ck->or_on = false;
  file_anchor(ck, a);
}

// The comparison reached the X-sync at the head of the queue.
static void at_anchor(bpc1_checker_t *ck) {
  bpc1_anchor_t a = ck->q[0];
  pop_anchor(ck);
  bool same = same_file(ck, &a);
  // A new file: after a LAST X-sync, at another identity, where the block
  // index goes back, or at an X-sync that is not TAIL after a TAIL one
  if (ck->last_last || !same || a.f.block < ck->last_k || (ck->tail_seen && !(a.flags & BPC1_F_TAIL))) {
    uint64_t au = (a.h + (1u << ck->shift) - 1u) >> ck->shift;
    finish_file(ck, ck->last_last ? BPC1_END_LAST : same ? BPC1_END_BACK : BPC1_END_NEXT, au);
    open_file(ck, &a);
    return;
  }
  file_anchor(ck, &a);
}

// The next position the comparison has to stop at: an X-sync, an overrun,
// the end of the LAST block
static uint64_t next_event(const bpc1_checker_t *ck) {
  uint32_t sh = ck->shift, one = (1u << sh) - 1u;
  uint64_t ev = UINT64_MAX;
  if (ck->nq) {
    ev = (ck->q[0].h + one) >> sh;
  }
  if (ck->nover && ck->over[0] < held_end(ck)) {
    ev = min64(ev, (ck->over[0] + one) >> sh);
  }
  if (size_ok(ck) && ck->state != ST_BODY) {
    ev = min64(ev, ck->end_u);
  }
  return ev;
}

static void cursor_step(bpc1_checker_t *ck, uint64_t limit) {
  uint64_t ph = ck->p << ck->shift;
  // At an X-sync due here the file ends at ph: an overrun at ph is left to
  // finish_file()
  bool anchor_due = ck->nq && ck->q[0].h <= ph;
  take_overruns(ck, min64(anchor_due ? ph : ph + 1u, held_end(ck)));
  take_rcount(ck, ph);
  if (size_ok(ck) && ck->p >= ck->end_u && ck->state != ST_BODY) {
    finish_file(ck, BPC1_END_LAST, ck->end_u);  // before the next file's X-sync there
    return;
  }
  if (anchor_due) {
    at_anchor(ck);
    return;
  }
  uint64_t lim = min64(limit, next_event(ck));
  if (ck->state == ST_BODY) {
    body_run(ck, lim);
    if (ck->end_known && ck->p >= ck->end_u && ck->state == ST_BODY) {
      body_complete(ck);  // the end of the LAST block of the body
    }
  } else {
    or_range(ck, ck->p, lim);
    ck->p = lim;
  }
}

// Opens the file of the X-sync at the head of the queue
static bool start_next(bpc1_checker_t *ck) {
  if (!ck->nq) {
    return false;
  }
  bpc1_anchor_t a = ck->q[0];
  pop_anchor(ck);
  if (ck->in_frames - (a.h >> 1) + RUN_EVERY > BPC1_RING_FRAMES) {
    return true;  // too old to compare from
  }
  open_file(ck, &a);
  return true;
}

static void cursor_run(bpc1_checker_t *ck, bool flush) {
  if (ck->allow_silence) {
    sil_watch(ck, ck->active ? max64(ck->cov_end, extent_end(ck)) : ck->cov_end);
    sil_look(ck);
  }
  for (;;) {
    if (!ck->active) {
      if (!start_next(ck)) {
        return;
      }
      continue;
    }
    uint64_t n = in_units(ck), lag = (uint64_t) (LAG_FRAMES << 1) >> ck->shift;
    uint64_t limit = flush ? n : (n > lag ? n - lag : 0);
    if (ck->p >= limit) {
      return;
    }
    cursor_step(ck, limit);
  }
}

//--------------------------------------------------------------------+
// Input: X-sync search, DoP markers, R-header decoding
//--------------------------------------------------------------------+

// h0..h4 of two headers are equal: every field of section 6 is
static bool same_header(const bpc1_fields_t *a, const bpc1_fields_t *b) {
  return a->version == b->version && a->kind == b->kind && a->depth_code == b->depth_code
         && a->rate_code == b->rate_code && a->channel == b->channel && a->nch == b->nch && a->flags == b->flags
         && a->seed == b->seed && a->block == b->block;
}

static void rdec_start(bpc1_checker_t *ck, const bpc1_anchor_t *a, const bpc1_fields_t x[]) {
  bpc1_rdec_t *d = &ck->rdec;
  bpc1_signal_t g;
  bpc1_fields_t f = a->f;
  f.nch = f.nch > BPC1_MAX_CH ? BPC1_MAX_CH : f.nch;
  if (d->active || !bpc1_signal_init(&g, &f)) {
    return;
  }
  bool dop = a->mode == MODE_DOP;
  uint32_t L = dop ? g.chip / 8u : g.chip;
  d->active = true;
  d->mode = a->mode;
  d->chans = a->chans;
  d->h = a->h;
  d->q = 0;
  d->step = L;
  d->next = (dop ? a->h + g.r_start / 8u : (a->h >> 1) + g.r_start) + L / 2u;
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    d->ok[c] = 1;
    d->prev[c] = 0;
    memset(d->r[c], 0, sizeof(d->r[c]));
    if (d->chans & (1u << c)) {
      d->x[c] = x[c];
    }
  }
}

// The chip centre at unit u of an R-header block: decode by the sign of the
// sample (PCM) or the byte 0xEE / 0x11 (DSD), 12.6, Extent of a file.
static void rdec_unit(bpc1_checker_t *ck, uint64_t u, const uint32_t w[], uint32_t byte_sh) {
  bpc1_rdec_t *d = &ck->rdec;
  if (!d->active || u != d->next) {
    return;
  }
  static const uint16_t none[7] = {0};
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    if (!(d->chans & (1u << c))) {
      continue;
    }
    int v;
    if (d->mode == MODE_DOP) {
      uint32_t b = (w[c] >> byte_sh) & 0xFFu;
      v = b == DSD_PLUS ? 1 : b == DSD_MINUS ? -1 : 0;
    } else {
      int32_t x = (int32_t) w[c];
      v = x > 0 ? 1 : x < 0 ? -1 : 0;
    }
    if (d->q < 13) {
      d->ok[c] = d->ok[c] && v == bpc1_chip(none, d->q);
    } else {
      uint32_t j = d->q - 13u;
      if (!(j & 1u)) {
        d->prev[c] = (int8_t) v;
      } else {
        d->ok[c] = d->ok[c] && d->prev[c] != 0 && v == -d->prev[c];
        d->r[c][j / 32u] = (uint16_t) ((d->r[c][j / 32u] << 1) | (d->prev[c] > 0));
      }
    }
  }
  d->q++;
  d->next += d->step;
  if (d->q == BPC1_N_CHIPS) {
    d->active = false;
    for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
      uint16_t total;
      bpc1_fields_t f;
      // r0 to r4 must equal h0 to h4 of the channel's X-sync (12.6, checker/verify.py _count):
      // reserved flag bits set in the R-header give no count
      if ((d->chans & (1u << c)) && d->ok[c] && bpc1_rhdr_parse(d->r[c], &f, &total)
          && same_header(&f, &d->x[c])) {
        d->done = true;
        d->f = f;
        d->total = total;
        d->seen = (ck->in_frames + 1u) << 1;
        break;
      }
    }
  }
}

static void found_sync(bpc1_checker_t *ck, uint64_t h, uint8_t mode, uint8_t chans, const bpc1_fields_t f[]) {
  bpc1_anchor_t a;
  memset(&a, 0, sizeof(a));
  a.h = h;
  a.mode = mode;
  // Channels must agree on the file and the block (checker/exact.py file_identity)
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    if (!(chans & (1u << c))) {
      continue;
    }
    if (a.chans && (!bpc1_same_file(&a.f, &f[c]) || a.f.block != f[c].block)) {
      continue;
    }
    if (!a.chans) {
      a.f = f[c];
    }
    a.flags |= f[c].flags;
    a.chans |= (uint8_t) (1u << c);
  }
  if (!a.chans || ck->nq == BPC1_MAX_ANCHORS) {
    return;
  }
  uint32_t x = ck->nq;
  while (x > 0 && ck->q[x - 1u].h > h) {
    ck->q[x] = ck->q[x - 1u];
    x--;
  }
  ck->q[x] = a;
  ck->nq++;
  if (bpc1_is_rhdr(a.f.block)) {
    rdec_start(ck, &a, f);
  }
}

// An X-sync candidate found by its second word (0x5EED): parsed from the ring
// once its last unit has arrived.
static void cand_add(bpc1_checker_t *ck, uint64_t start, uint8_t mode, uint64_t due) {
  for (uint32_t x = 0; x < ck->ncand; x++) {
    if (ck->cand[x].start == start && ck->cand[x].mode == mode) {
      return;
    }
  }
  if (ck->ncand < BPC1_MAX_CAND) {
    ck->cand[ck->ncand].start = start;
    ck->cand[ck->ncand].mode = mode;
    ck->cand[ck->ncand].due = due;
    ck->ncand++;
  }
}

// The eight X-sync words of channel c from unit start
static void sync_words(const bpc1_checker_t *ck, uint64_t start, uint8_t mode, uint32_t c, uint16_t words[8]) {
  for (uint32_t i = 0; i < 8; i++) {
    if (mode == MODE_DOP) {
      uint64_t u = start + 2u * i;
      uint32_t hi = ck->ring[(uint32_t) (u >> 1) & RMASK][c] >> ((u & 1u) ? 8u : 16u);
      uint32_t lo = ck->ring[(uint32_t) ((u + 1u) >> 1) & RMASK][c] >> ((u & 1u) ? 16u : 8u);
      words[i] = (uint16_t) (((hi & 0xFFu) << 8) | (lo & 0xFFu));
    } else {
      words[i] = (uint16_t) (ck->ring[(uint32_t) (start + i) & RMASK][c] >> 16);
    }
  }
}

static void cand_parse(bpc1_checker_t *ck, uint64_t start, uint8_t mode) {
  uint8_t chans = 0;
  bpc1_fields_t f[BPC1_MAX_CH];
  for (uint32_t c = 0; c < BPC1_MAX_CH; c++) {
    uint16_t words[8];
    sync_words(ck, start, mode, c, words);
    if (bpc1_sync_parse(words, &f[c]) && !(f[c].flags & ~(BPC1_F_LEAD_IN | BPC1_F_TAIL | BPC1_F_LAST))) {
      chans |= (uint8_t) (1u << c);  // as generated: reserved flag bits 0
    }
  }
  if (chans) {
    found_sync(ck, mode == MODE_DOP ? start : start << 1, mode, chans, f);
  }
}

// The eight DoP frames of an X-sync from frame f0: markers 0x05 / 0xFA
// alternating, the same in every channel
static bool dop_markers(const bpc1_checker_t *ck, uint64_t f0) {
  uint32_t prev = 0;
  for (uint64_t f = f0; f < f0 + 8u; f++) {
    const uint32_t *w = ck->ring[(uint32_t) f & RMASK];
    uint32_t m = w[0] >> 24;
    if ((m != 0x05u && m != 0xFAu) || (w[1] >> 24) != m || m == prev) {
      return false;
    }
    prev = m;
  }
  return true;
}

// The next frame slow_frame() has to see: a candidate due, or the next chip
// centre of the R-header being decoded.
static void plan_slow(bpc1_checker_t *ck) {
  uint64_t at = UINT64_MAX;
  for (uint32_t x = 0; x < ck->ncand; x++) {
    at = min64(at, ck->cand[x].due);
  }
  if (ck->rdec.active) {
    at = min64(at, ck->rdec.mode == MODE_DOP ? ck->rdec.next >> 1 : ck->rdec.next);
  }
  ck->slow_at = at;
}

// Frame n holds an X-sync candidate (hit), a candidate due, or a chip centre
// of the R-header being decoded.
NOINLINE static void slow_frame(bpc1_checker_t *ck, uint64_t n, const uint32_t *w, bool hit) {
  ck->in_frames = n;  // frames before this one
  for (uint32_t c = 0; c < BPC1_MAX_CH && hit; c++) {
    uint32_t x = (w[c] >> 8) & 0xFFFFu;
    if (n >= 1 && (w[c] >> 16) == BPC1_MAGIC1) {
      cand_add(ck, n - 1u, MODE_PCM, n + 6u);
    }
    if (ck->slot_bits >= 24 && n >= 1 && x == BPC1_MAGIC1) {
      cand_add(ck, 2u * n - 2u, MODE_DOP, n + 6u);  // 5E ED in this frame, B1 C0 in the one before
    }
  }
  for (uint32_t x = 0; x < ck->ncand;) {
    if (ck->cand[x].due == n) {
      uint64_t start = ck->cand[x].start;
      uint8_t mode = ck->cand[x].mode;
      ck->cand[x] = ck->cand[--ck->ncand];
      if (mode != MODE_DOP || dop_markers(ck, start >> 1)) {
        cand_parse(ck, start, mode);
      }
    } else {
      x++;
    }
  }
  if (ck->rdec.active) {
    if (ck->rdec.mode == MODE_DOP) {
      rdec_unit(ck, 2u * n, w, 16);
      rdec_unit(ck, 2u * n + 1u, w, 8);
    } else {
      rdec_unit(ck, n, w, 0);
    }
  }
  plan_slow(ck);
}

// Frames from n0 to the next one slow_frame() has to see, at most m
static inline uint32_t slow_next(const bpc1_checker_t *ck, uint64_t n0, uint32_t m) {
  uint64_t at = ck->slow_at - n0;
  return at < m ? (uint32_t) at : m;
}

// The rare frames of push_frames(): frame n0 + t to slow_frame(). Returns the
// next frame, from n0, that it has to see.
NOINLINE static uint32_t push_slow(bpc1_checker_t *ck, uint64_t n0, uint32_t t, const uint32_t *f, bool hit,
                                   uint32_t m) {
  slow_frame(ck, n0 + t, f, hit);
  return slow_next(ck, n0, m);
}

// The PCM X-sync word 0x5EED in the top 16 bits of w
static inline uint32_t pcm_magic(uint32_t w) {
  return (w >> 16) == BPC1_MAGIC1;
}

// Stores m frames and looks for the second X-sync word, 0x5EED: in the top 16
// bits (PCM), and in 3- and 4-byte slots in the DoP bytes of a frame.
static void push_frames(bpc1_checker_t *ck, const uint32_t *w, uint32_t m) {
  uint64_t n0 = ck->in_frames;
  uint32_t (*ring)[BPC1_MAX_CH] = ck->ring;
  uint32_t idx = (uint32_t) n0;
  uint32_t next = slow_next(ck, n0, m);
  if (ck->slot_bits < 24) {
    for (uint32_t t = 0; t < m; t++) {
      const uint32_t *f = &w[BPC1_MAX_CH * t];
      uint32_t w0 = f[0], w1 = f[1];
      uint32_t *slot = ring[(idx + t) & RMASK];
      slot[0] = w0;
      slot[1] = w1;
      uint32_t hit = pcm_magic(w0) | pcm_magic(w1);
      if (RARE(hit | (t >= next))) {
        next = push_slow(ck, n0, t, f, hit != 0, m);
      }
    }
    ck->in_frames = n0 + m;
    return;
  }
  for (uint32_t t = 0; t < m; t++) {
    const uint32_t *f = &w[BPC1_MAX_CH * t];
    uint32_t w0 = f[0], w1 = f[1];
    uint32_t *slot = ring[(idx + t) & RMASK];
    slot[0] = w0;
    slot[1] = w1;
    uint32_t mk = w0 >> 24;
    uint32_t hit = pcm_magic(w0) | pcm_magic(w1);
    if (mk == 0x05u || mk == 0xFAu) {
      // a DoP marker: the 0x5EED of a DoP X-sync
      hit |= (((w0 >> 8) & 0xFFFFu) == BPC1_MAGIC1) | (((w1 >> 8) & 0xFFFFu) == BPC1_MAGIC1);
    }
    if (RARE(hit | (t >= next))) {
      next = push_slow(ck, n0, t, f, hit != 0, m);
    }
  }
  ck->in_frames = n0 + m;
}

//--------------------------------------------------------------------+
// API
//--------------------------------------------------------------------+

void bpc1_checker_init(bpc1_checker_t *ck, uint32_t host_rate, uint8_t slot_bits) {
  memset(ck, 0, sizeof(*ck));
  ck->host_rate = host_rate;
  ck->slot_bits = slot_bits;
  ck->shift = 1;
  ck->slow_at = UINT64_MAX;
  ck->sil_ns = UINT64_MAX;
  for (uint32_t v = 0; v < 256; v++) {
    uint32_t x = 0;
    for (uint32_t j = 0; j < 8; j++) {
      uint32_t b = (v >> (7u - j)) & 1u;
      x |= ((b << 1) | (b ^ 1u)) << (14u - 2u * j);
    }
    pairs16[v] = (uint16_t) x;
  }
}

void bpc1_checker_feed(bpc1_checker_t *ck, const uint32_t *words, uint32_t n_frames) {
  while (n_frames) {
    uint32_t room = RUN_EVERY - ((uint32_t) ck->in_frames & (RUN_EVERY - 1u));
    uint32_t m = n_frames < room ? n_frames : room;
    push_frames(ck, words, m);
    words += BPC1_MAX_CH * m;
    n_frames -= m;
    if ((ck->in_frames & (RUN_EVERY - 1u)) == 0) {
      cursor_run(ck, false);
    }
  }
}

void bpc1_checker_overrun(bpc1_checker_t *ck) {
  if (ck->nover < BPC1_MAX_OVERRUNS) {
    ck->over[ck->nover++] = ck->in_frames << 1;
  } else {
    ck->over[BPC1_MAX_OVERRUNS - 1u] = ck->in_frames << 1;  // the stretch it falls in still knows of one
  }
}

bool bpc1_checker_pop(bpc1_checker_t *ck, bpc1_report_t *rep) {
  if (!ck->nout) {
    return false;
  }
  *rep = ck->out[ck->out_head];
  ck->out_head = (uint8_t) ((ck->out_head + 1u) % BPC1_MAX_REPORTS);
  ck->nout--;
  return true;
}

void bpc1_checker_end(bpc1_checker_t *ck) {
  cursor_run(ck, true);
  if (ck->active) {
    // An overrun at the stream's end stands before a unit that never came:
    // a cut file holds it, where the stream stayed inside its extent
    finish_file(ck, ck->last_last ? BPC1_END_LAST : BPC1_END_STOP, in_units(ck));  // its LAST X-sync arrived
  }
  // Overruns still queued, after a complete file where the stream ended
  // inside its extent, are no result's (12.6). The last result holds the
  // stream's end unless so, or unless the stretch after it is allowed
  // silence, without a result.
  ck->end_held = !ck->fin_complete;
  if (ck->cov_end < ck->in_frames) {
    ck->end_held = unknown_stretch(ck, ck->cov_end, ck->in_frames, ck->nover != 0);
    ck->cov_end = ck->in_frames;
  }
  ck->nover = 0;
}

uint64_t bpc1_checker_outside(const bpc1_checker_t *ck) {
  return ck->outside;
}

uint64_t bpc1_checker_silent(const bpc1_checker_t *ck) {
  return ck->silent;
}

bool bpc1_checker_end_held(const bpc1_checker_t *ck) {
  return ck->end_held;
}

uint64_t bpc1_checker_covered(const bpc1_checker_t *ck) {
  return ck->cov_end;
}

uint32_t bpc1_checker_results_lost(const bpc1_checker_t *ck) {
  return ck->out_lost;
}

void bpc1_checker_allow_silence(bpc1_checker_t *ck, bool allow) {
  ck->allow_silence = allow;
}

bool bpc1_checker_peek(const bpc1_checker_t *ck, bpc1_report_t *rep) {
  if (!ck->active) {
    return false;
  }
  fill_report(ck, BPC1_END_OPEN, ck->p, rep);
  return true;
}

static const char *name_of(const char *const names[], uint32_t n, uint32_t x) {
  return x < n ? names[x] : "?";
}

#define NAME_OF(names, x) name_of(names, sizeof(names) / sizeof(names[0]), x)

const char *bpc1_verdict_name(uint8_t verdict) {
  static const char *const names[] = {"UNKNOWN_SIGNAL", "BIT_PERFECT", "NOT_BIT_PERFECT", "OVERRUN"};
  return NAME_OF(names, verdict);
}

const char *bpc1_kind_name(uint8_t kind) {
  static const char *const names[] = {"NONE", "PCM", "DSD", "?", "?", "SWEEP"};
  return NAME_OF(names, kind);
}

const char *bpc1_end_name(uint8_t end) {
  static const char *const names[] = {"OPEN", "LAST", "NEXT", "BACK", "STOP"};
  return NAME_OF(names, end);
}

const char *bpc1_why_name(uint8_t why) {
  static const char *const names[] = {"none", "rate", "start", "first", "unit", "marker",
                                      "chip", "crc",  "count", "left",  "end"};
  return NAME_OF(names, why);
}

void bpc1_diff_text(const bpc1_report_t *r, char *buf, uint32_t size) {
  if (size == 0) {
    return;
  }
  if (!r->diff_known) {
    buf[0] = size > 1 ? '-' : '\0';
    if (size > 1) {
      buf[1] = '\0';
    }
    return;
  }
  // seconds, 3 decimals, without printf
  char tmp[16];
  uint32_t n = 0, v = r->diff_ms;
  for (uint32_t d = 0; d < 3; d++) {
    tmp[n++] = (char) ('0' + v % 10u);
    v /= 10u;
  }
  tmp[n++] = '.';
  do {
    tmp[n++] = (char) ('0' + v % 10u);
    v /= 10u;
  } while (v && n < sizeof(tmp));
  uint32_t o = 0;
  while (n && o + 1u < size) {
    buf[o++] = tmp[--n];
  }
  buf[o] = '\0';
}
