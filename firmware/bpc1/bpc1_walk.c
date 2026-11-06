// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The frame walker of the BPC1 generator: the frames of a BPC1 file, run by
// run, and the number table of the published files (docs/bpc1.md 11).

#include "bpc1_walk.h"

#include <string.h>

//--------------------------------------------------------------------+
// The number table (docs/bpc1.md 11.2): a table, not a formula, since a
// file added later need not follow the rules. Numbers are T O F PP with
// F = 0; a row's formats are the F digits it is published in, all holding
// the same units. Each row names its file, without "bpc1_".
//--------------------------------------------------------------------+

#define F_WAV     0x01u  // WAV only
#define F_PCM     0x0Fu  // WAV, FLAC, AIFF, ALAC
#define F_DSD     0x3Fu  // DoP as WAV, FLAC, AIFF, ALAC; DSF; DFF
#define F_DSF_DFF 0x30u  // DSF, DFF

typedef struct {
  uint32_t number;
  uint8_t formats;
  bpc1_file_t file;
} row_t;

#define ROW_FILE(n, fm, kind, dc, rc, lead, body, tail) \
  {n, fm, {kind, dc, rc, BPC1_FAULT_NONE, lead, body, tail, 0, 0, 0, 0}}
#define ROW_PART(n, fm, kind, dc, rc, lead, body, tail, part, cut) \
  {n, fm, {kind, dc, rc, BPC1_FAULT_NONE, lead, body, tail, part, cut, 0, 0}}
#define ROW_NEG(n, fm, kind, dc, rc, lead, body, tail, fault, part, cut, at, len) \
  {n, fm, {kind, dc, rc, BPC1_FAULT_##fault, lead, body, tail, part, cut, at, len}}

static const row_t table[] = {
  ROW_FILE(0, F_PCM, BPC1_KIND_PCM, 2, 0, 22, 108, 6),  // pcm_044k1_16bit
  ROW_FILE(1, F_PCM, BPC1_KIND_PCM, 2, 1, 24, 118, 6),  // pcm_048k_16bit
  ROW_FILE(2, F_PCM, BPC1_KIND_PCM, 2, 2, 22, 108, 6),  // pcm_088k2_16bit
  ROW_FILE(3, F_PCM, BPC1_KIND_PCM, 2, 3, 24, 118, 6),  // pcm_096k_16bit
  ROW_FILE(4, F_PCM, BPC1_KIND_PCM, 2, 4, 22, 108, 6),  // pcm_176k4_16bit
  ROW_FILE(5, F_PCM, BPC1_KIND_PCM, 2, 5, 24, 118, 6),  // pcm_192k_16bit
  ROW_FILE(6, F_PCM, BPC1_KIND_PCM, 2, 6, 22, 108, 6),  // pcm_352k8_16bit
  ROW_FILE(7, F_PCM, BPC1_KIND_PCM, 2, 7, 24, 118, 6),  // pcm_384k_16bit
  ROW_FILE(8, F_PCM, BPC1_KIND_PCM, 2, 8, 22, 108, 6),  // pcm_705k6_16bit
  ROW_FILE(9, F_PCM, BPC1_KIND_PCM, 2, 9, 24, 118, 6),  // pcm_768k_16bit
  ROW_FILE(30, F_PCM, BPC1_KIND_PCM, 3, 0, 22, 108, 6),  // pcm_044k1_24bit
  ROW_FILE(31, F_PCM, BPC1_KIND_PCM, 3, 1, 24, 118, 6),  // pcm_048k_24bit
  ROW_FILE(32, F_PCM, BPC1_KIND_PCM, 3, 2, 22, 108, 6),  // pcm_088k2_24bit
  ROW_FILE(33, F_PCM, BPC1_KIND_PCM, 3, 3, 24, 118, 6),  // pcm_096k_24bit
  ROW_FILE(34, F_PCM, BPC1_KIND_PCM, 3, 4, 22, 108, 6),  // pcm_176k4_24bit
  ROW_FILE(35, F_PCM, BPC1_KIND_PCM, 3, 5, 24, 118, 6),  // pcm_192k_24bit
  ROW_FILE(36, F_PCM, BPC1_KIND_PCM, 3, 6, 22, 108, 6),  // pcm_352k8_24bit
  ROW_FILE(37, F_PCM, BPC1_KIND_PCM, 3, 7, 24, 118, 6),  // pcm_384k_24bit
  ROW_FILE(38, F_PCM, BPC1_KIND_PCM, 3, 8, 22, 108, 6),  // pcm_705k6_24bit
  ROW_FILE(39, F_PCM, BPC1_KIND_PCM, 3, 9, 24, 118, 6),  // pcm_768k_24bit
  ROW_FILE(60, F_WAV, BPC1_KIND_PCM, 4, 0, 22, 108, 6),  // pcm_044k1_32bit
  ROW_FILE(61, F_WAV, BPC1_KIND_PCM, 4, 1, 24, 118, 6),  // pcm_048k_32bit
  ROW_FILE(62, F_WAV, BPC1_KIND_PCM, 4, 2, 22, 108, 6),  // pcm_088k2_32bit
  ROW_FILE(63, F_WAV, BPC1_KIND_PCM, 4, 3, 24, 118, 6),  // pcm_096k_32bit
  ROW_FILE(64, F_WAV, BPC1_KIND_PCM, 4, 4, 22, 108, 6),  // pcm_176k4_32bit
  ROW_FILE(65, F_WAV, BPC1_KIND_PCM, 4, 5, 24, 118, 6),  // pcm_192k_32bit
  ROW_FILE(66, F_WAV, BPC1_KIND_PCM, 4, 6, 22, 108, 6),  // pcm_352k8_32bit
  ROW_FILE(67, F_WAV, BPC1_KIND_PCM, 4, 7, 24, 118, 6),  // pcm_384k_32bit
  ROW_FILE(68, F_WAV, BPC1_KIND_PCM, 4, 8, 22, 108, 6),  // pcm_705k6_32bit
  ROW_FILE(69, F_WAV, BPC1_KIND_PCM, 4, 9, 24, 118, 6),  // pcm_768k_32bit
  ROW_FILE(1000, F_DSD, BPC1_KIND_DSD, 1, 10, 22, 108, 6),  // dsd064
  ROW_FILE(1001, F_DSD, BPC1_KIND_DSD, 1, 11, 22, 108, 6),  // dsd128
  ROW_FILE(1002, F_DSD, BPC1_KIND_DSD, 1, 12, 22, 108, 6),  // dsd256
  ROW_FILE(10000, F_PCM, BPC1_KIND_SWEEP, 2, 0, 22, 108, 6),  // pcm_044k1_16bit_sweep
  ROW_FILE(10001, F_PCM, BPC1_KIND_SWEEP, 2, 1, 24, 118, 6),  // pcm_048k_16bit_sweep
  ROW_FILE(10002, F_PCM, BPC1_KIND_SWEEP, 2, 2, 22, 108, 6),  // pcm_088k2_16bit_sweep
  ROW_FILE(10003, F_PCM, BPC1_KIND_SWEEP, 2, 3, 24, 118, 6),  // pcm_096k_16bit_sweep
  ROW_FILE(10004, F_PCM, BPC1_KIND_SWEEP, 2, 4, 22, 108, 6),  // pcm_176k4_16bit_sweep
  ROW_FILE(10005, F_PCM, BPC1_KIND_SWEEP, 2, 5, 24, 118, 6),  // pcm_192k_16bit_sweep
  ROW_FILE(10006, F_PCM, BPC1_KIND_SWEEP, 2, 6, 22, 108, 6),  // pcm_352k8_16bit_sweep
  ROW_FILE(10007, F_PCM, BPC1_KIND_SWEEP, 2, 7, 24, 118, 6),  // pcm_384k_16bit_sweep
  ROW_FILE(10008, F_PCM, BPC1_KIND_SWEEP, 2, 8, 22, 108, 6),  // pcm_705k6_16bit_sweep
  ROW_FILE(10009, F_PCM, BPC1_KIND_SWEEP, 2, 9, 24, 118, 6),  // pcm_768k_16bit_sweep
  ROW_FILE(10030, F_PCM, BPC1_KIND_SWEEP, 3, 0, 22, 4448, 6),  // pcm_044k1_24bit_sweep
  ROW_FILE(10031, F_PCM, BPC1_KIND_SWEEP, 3, 1, 24, 4448, 6),  // pcm_048k_24bit_sweep
  ROW_FILE(10032, F_PCM, BPC1_KIND_SWEEP, 3, 2, 22, 2205, 6),  // pcm_088k2_24bit_sweep
  ROW_FILE(10033, F_PCM, BPC1_KIND_SWEEP, 3, 3, 24, 2205, 6),  // pcm_096k_24bit_sweep
  ROW_FILE(10034, F_PCM, BPC1_KIND_SWEEP, 3, 4, 22, 1098, 6),  // pcm_176k4_24bit_sweep
  ROW_FILE(10035, F_PCM, BPC1_KIND_SWEEP, 3, 5, 24, 1098, 6),  // pcm_192k_24bit_sweep
  ROW_FILE(10036, F_PCM, BPC1_KIND_SWEEP, 3, 6, 22, 549, 6),  // pcm_352k8_24bit_sweep
  ROW_FILE(10037, F_PCM, BPC1_KIND_SWEEP, 3, 7, 24, 549, 6),  // pcm_384k_24bit_sweep
  ROW_FILE(10038, F_PCM, BPC1_KIND_SWEEP, 3, 8, 22, 274, 6),  // pcm_705k6_24bit_sweep
  ROW_FILE(10039, F_PCM, BPC1_KIND_SWEEP, 3, 9, 24, 275, 6),  // pcm_768k_24bit_sweep
  ROW_PART(20000, F_PCM, BPC1_KIND_PCM, 2, 0, 22, 108, 6, 1, 330751),  // pcm_044k1_16bit_gapless_part1
  ROW_PART(20001, F_PCM, BPC1_KIND_PCM, 2, 0, 22, 108, 6, 2, 330751),  // pcm_044k1_16bit_gapless_part2
  ROW_PART(20002, F_PCM, BPC1_KIND_PCM, 2, 1, 24, 118, 6, 1, 360001),  // pcm_048k_16bit_gapless_part1
  ROW_PART(20003, F_PCM, BPC1_KIND_PCM, 2, 1, 24, 118, 6, 2, 360001),  // pcm_048k_16bit_gapless_part2
  ROW_PART(20030, F_PCM, BPC1_KIND_PCM, 3, 0, 22, 108, 6, 1, 330751),  // pcm_044k1_24bit_gapless_part1
  ROW_PART(20031, F_PCM, BPC1_KIND_PCM, 3, 0, 22, 108, 6, 2, 330751),  // pcm_044k1_24bit_gapless_part2
  ROW_PART(20032, F_PCM, BPC1_KIND_PCM, 3, 1, 24, 118, 6, 1, 360001),  // pcm_048k_24bit_gapless_part1
  ROW_PART(20033, F_PCM, BPC1_KIND_PCM, 3, 1, 24, 118, 6, 2, 360001),  // pcm_048k_24bit_gapless_part2
  ROW_PART(21000, F_DSF_DFF, BPC1_KIND_DSD, 1, 10, 22, 108, 6, 1, 1323001),  // dsd064_gapless_part1
  ROW_PART(21001, F_DSF_DFF, BPC1_KIND_DSD, 1, 10, 22, 108, 6, 2, 1323001),  // dsd064_gapless_part2
  ROW_NEG(90000, F_WAV, BPC1_KIND_PCM, 3, 0, 22, 108, 6, ALTERED, 0, 0, 0, 0),  // pcm_044k1_24bit_altered
  // pcm_044k1_24bit_incomplete
  ROW_NEG(90001, F_WAV, BPC1_KIND_PCM, 3, 0, 22, 108, 6, INCOMPLETE, 0, 0, 107520, 1000),
  // pcm_044k1_16bit_sweep_altered
  ROW_NEG(90002, F_WAV, BPC1_KIND_SWEEP, 2, 0, 22, 108, 6, ALTERED, 0, 0, 0, 0),
  // pcm_044k1_16bit_gapless_part1_gap
  ROW_NEG(90003, F_WAV, BPC1_KIND_PCM, 2, 0, 22, 108, 6, GAP, 1, 330751, 0, 441),
  // pcm_044k1_16bit_gapless_part2_gap
  ROW_NEG(90004, F_WAV, BPC1_KIND_PCM, 2, 0, 22, 108, 6, GAP, 2, 330751, 0, 441),
  // dsd064_dop-176k4_24bit_altered
  ROW_NEG(91000, F_WAV, BPC1_KIND_DSD, 1, 10, 22, 108, 6, ALTERED, 0, 0, 427008, 1000),
  // dsd064_dop-176k4_24bit_incomplete
  ROW_NEG(91001, F_WAV, BPC1_KIND_DSD, 1, 10, 22, 108, 6, INCOMPLETE, 0, 0, 427008, 1000),
};
#define N_ROWS (sizeof(table) / sizeof(table[0]))

bool bpc1_walk_lookup(uint32_t number, bpc1_file_t *f) {
  if (number > 99999u) {
    return false;
  }
  uint32_t fmt = number / 100u % 10u;
  uint32_t base = number - 100u * fmt;
  for (uint32_t r = 0; r < N_ROWS; r++) {
    if (table[r].number == base && (table[r].formats & (1u << fmt)) != 0) {
      *f = table[r].file;
      return true;
    }
  }
  return false;
}

//--------------------------------------------------------------------+
// Setup
//--------------------------------------------------------------------+

static uint8_t flags_of(const bpc1_walk_t *w, uint32_t k) {
  uint8_t f = 0;
  if (k < w->file.lead) {
    f |= BPC1_F_LEAD_IN;
  } else if (k >= (uint32_t) w->file.lead + w->file.body) {
    f |= BPC1_F_TAIL;
  }
  if (k + 1u == w->total) {
    f |= BPC1_F_LAST;
  }
  return f;
}

static bool setup(bpc1_walk_t *w, const bpc1_file_t *f, uint8_t slot) {
  const uint16_t h[5] = {
    (uint16_t) ((BPC1_VERSION << 12) | ((uint32_t) f->kind << 8) | ((uint32_t) f->depth_code << 4)
                | f->rate_code),
    (uint16_t) (2u << 8),  // channel 0 of 2, no flags
    (uint16_t) (BPC1_WALK_SEED >> 16),
    (uint16_t) BPC1_WALK_SEED,
    0,
  };
  bpc1_fields_t fields;
  uint32_t total = (uint32_t) f->lead + f->body + f->tail;
  if (slot < 2 || slot > 4 || !bpc1_fields_parse(h, &fields) || total == 0 || total > 0xFFFFu
      || f->fault > BPC1_FAULT_GAP || f->part > 2 || !bpc1_signal_init(&w->sig, &fields)) {
    return false;
  }
  w->file = *f;
  w->dop = w->sig.dsd;
  w->slot = slot;
  w->nch = fields.nch;
  w->total = total;
  w->fpb = w->dop ? w->sig.block_len / 16u : w->sig.block_len;
  if ((w->dop ? 24u : w->sig.depth) > 8u * slot || total > 0xFFFFFFFFu / w->fpb) {
    return false;
  }

  // What to send of the regular file: a part, a gap, frames left out
  uint32_t frames = total * w->fpb;
  w->end = frames;
  if (f->part != 0) {
    if (f->cut == 0 || f->cut >= frames) {
      return false;
    }
    if (f->part == 1) {
      w->end = f->cut;
    } else {
      w->pos = f->cut;
    }
  }
  if (f->fault == BPC1_FAULT_GAP && f->part == 2) {
    w->zeros = f->len;
  }
  w->frames = w->end - w->pos + w->zeros;
  if (f->fault == BPC1_FAULT_INCOMPLETE) {
    if (f->len == 0 || f->at < w->pos || f->at > w->end || f->len > w->end - f->at) {
      return false;
    }
    w->frames -= f->len;
  }

  // The walking-bit burst depends on the depth and channel only
  if (!w->dop) {
    bpc1_block_init(&w->sig, 0, 0, true, (uint16_t) total, &w->blk);
    for (uint32_t c = 0; c < w->nch; c++) {
      for (uint32_t j = 0; j < 64; j++) {
        w->walk[c][j] = bpc1_pcm_unit(&w->sig, &w->blk, c, BPC1_SYNC_FRAMES + j);
      }
    }
  }
  return true;
}

bool bpc1_walk_init(bpc1_walk_t *w, const bpc1_file_t *f, uint8_t slot) {
  memset(w, 0, sizeof(*w));
  if (!setup(w, f, slot)) {
    memset(w, 0, sizeof(*w));  // nothing to send
    return false;
  }
  return true;
}

bool bpc1_walk_init_number(bpc1_walk_t *w, uint32_t number, uint8_t slot) {
  bpc1_file_t f;
  if (!bpc1_walk_lookup(number, &f)) {
    memset(w, 0, sizeof(*w));
    return false;
  }
  return bpc1_walk_init(w, &f, slot);
}

//--------------------------------------------------------------------+
// Generation: one block's frames i to i + n - 1 as words W (the PCM word,
// or the DoP sample << 8), channels interleaved; then the slots.
//--------------------------------------------------------------------+

#define STAGE 32u  // frames per pass

static inline uint32_t lb32(uint32_t key, uint32_t n) {
  uint32_t x = n ^ key;
  x ^= x >> 16;
  x *= 0x7FEB352Du;
  x ^= x >> 15;
  x *= 0x846CA68Bu;
  x ^= x >> 16;
  return x;
}

static inline uint32_t min32(uint32_t a, uint32_t b) {
  return a < b ? a : b;
}

static void gen_pcm(const bpc1_walk_t *w, uint32_t *st, uint32_t k, uint32_t i, uint32_t n) {
  const bpc1_signal_t *s = &w->sig;
  const uint32_t nch = w->nch, shift = 32u - s->depth, e = i + n, base = k * s->block_len;
  while (i < e) {
    if (i < BPC1_SYNC_FRAMES) {
      // X-sync: the word in the high 16 bits, lb32 below it, cut to the depth
      uint32_t stop = min32(e, BPC1_SYNC_FRAMES);
      uint32_t low = shift == 0 ? 0xFFFFFFFFu : ~((1u << shift) - 1u);
      for (; i < stop; i++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = (((uint32_t) w->blk.sync[c][i] << 16) | (lb32(s->keys[c], base + i) >> 16)) & low;
        }
      }
    } else if (i < BPC1_BURST_END) {
      uint32_t stop = min32(e, BPC1_BURST_END);
      for (; i < stop; i++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = w->walk[c][i - BPC1_SYNC_FRAMES];
        }
      }
    } else if (bpc1_is_rhdr(k)) {
      // Zeros, then the 237 chips of the R-header, each a run of L frames
      uint32_t stop = e;
      uint32_t v[BPC1_MAX_CH] = {0};
      if (i < s->r_start) {
        stop = min32(e, s->r_start);
      } else {
        uint32_t q = (i - s->r_start) / s->chip;
        if (q < BPC1_N_CHIPS) {
          stop = min32(e, s->r_start + (q + 1u) * s->chip);
          for (uint32_t c = 0; c < nch; c++) {
            v[c] = bpc1_chip(w->blk.rhdr[c], q) > 0 ? 0x20000000u : 0xE0000000u;
          }
        }
      }
      for (; i < stop; i++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = v[c];
        }
      }
    } else if (s->sweep) {
      uint32_t idx = bpc1_sweep_index(k, s->block_len, i);
      for (; i < e; i++, idx++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = bpc1_lbd(s->depth, s->sweep_keys[c], idx) << shift;
        }
      }
    } else {
      const uint32_t down = 32u - s->active;
      for (; i < e; i++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = (uint32_t) ((int32_t) lb32(s->keys[c], base + i) >> down) << shift;
        }
      }
    }
  }
}

// A nibble of hash bits, earliest as MSB, as DSD bits: each hash bit x as
// the pair x, not x
static const uint8_t pairs[16] = {
  0x55, 0x56, 0x59, 0x5A, 0x65, 0x66, 0x69, 0x6A, 0x95, 0x96, 0x99, 0x9A, 0xA5, 0xA6, 0xA9, 0xAA,
};

// DoP frames j to j + n - 1 of block k: frame j carries DSD bytes 2j and
// 2j + 1 of each channel (docs/bpc1.md 9.5); the marker is set by fill().
static void gen_dop(const bpc1_walk_t *w, uint32_t *st, uint32_t k, uint32_t j, uint32_t n) {
  const bpc1_signal_t *s = &w->sig;
  const uint32_t nch = w->nch, e = j + n;
  while (j < e) {
    if (j < BPC1_SYNC_BYTES / 2u) {
      uint32_t stop = min32(e, BPC1_SYNC_BYTES / 2u);
      for (; j < stop; j++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = (uint32_t) w->blk.sync[c][j] << 8;
        }
      }
    } else if (bpc1_is_rhdr(k)) {
      // DSD idle, then the chips, each chip / 16 frames of 1110 or 0001
      const uint32_t a = s->r_start / 16u, per = s->chip / 16u;
      uint32_t stop = e;
      uint32_t v[BPC1_MAX_CH] = {0x696900u, 0x696900u};
      if (j < a) {
        stop = min32(e, a);
      } else {
        uint32_t q = (j - a) / per;
        if (q < BPC1_N_CHIPS) {
          stop = min32(e, a + (q + 1u) * per);
          for (uint32_t c = 0; c < nch; c++) {
            v[c] = bpc1_chip(w->blk.rhdr[c], q) > 0 ? 0xEEEE00u : 0x111100u;
          }
        }
      }
      for (; j < stop; j++, st += nch) {
        for (uint32_t c = 0; c < nch; c++) {
          st[c] = v[c];
        }
      }
    } else {
      // One hash per 64 DSD bits, 4 DoP frames: frame j takes its byte j % 4
      const uint32_t base = (k * s->block_len) >> 6;
      while (j < e) {
        uint32_t h[BPC1_MAX_CH];
        for (uint32_t c = 0; c < nch; c++) {
          h[c] = lb32(s->keys[c], base + (j >> 2));
        }
        do {
          uint32_t sh = 24u - 8u * (j & 3u);
          for (uint32_t c = 0; c < nch; c++) {
            uint32_t b = (h[c] >> sh) & 0xFFu;
            st[c] = ((uint32_t) pairs[b >> 4] << 16) | ((uint32_t) pairs[b & 15u] << 8);
          }
          j++;
          st += nch;
        } while (j < e && (j & 3u) != 0);
      }
    }
  }
}

// A negative file's change to the words of frames f to f + n - 1 (frames
// of the regular file) of block k
static void alter(const bpc1_walk_t *w, uint32_t *st, uint32_t k, uint32_t f, uint32_t n) {
  const bpc1_file_t *file = &w->file;
  const uint32_t nch = w->nch;
  if (w->dop) {
    // DoP frames at..at+len-1: the 16 DSD bits inverted, the marker kept
    uint32_t a = f > file->at ? f : file->at;
    uint32_t b = min32(f + n, file->at + file->len);
    for (uint32_t x = a; x < b; x++) {
      for (uint32_t c = 0; c < nch; c++) {
        st[(x - f) * nch + c] ^= 0xFFFF00u;
      }
    }
    return;
  }
  if (k < file->lead || k >= (uint32_t) file->lead + file->body) {
    return;  // PCM and sweep: body blocks only
  }
  const uint32_t shift = 32u - w->sig.depth;
  if (w->sig.sweep) {
    // Channel 0's code 2^(D-1) - 2 becomes the next code
    const uint32_t code = ((1u << (w->sig.depth - 1u)) - 2u) << shift;
    for (uint32_t x = 0; x < n; x++) {
      if (st[x * nch] == code) {
        st[x * nch] += 1u << shift;
      }
    }
  } else {
    for (uint32_t x = 0; x < n * nch; x++) {
      st[x] &= ~(0xFFu << shift);  // the low 8 bits of the sample
    }
  }
}

static uint8_t *pack(const uint32_t *st, uint32_t n, uint32_t slot, uint8_t *o) {
  switch (slot) {
    case 4:
      for (uint32_t x = 0; x < n; x++, o += 4) {
        o[0] = (uint8_t) st[x];
        o[1] = (uint8_t) (st[x] >> 8);
        o[2] = (uint8_t) (st[x] >> 16);
        o[3] = (uint8_t) (st[x] >> 24);
      }
      break;
    case 3:
      for (uint32_t x = 0; x < n; x++, o += 3) {
        o[0] = (uint8_t) (st[x] >> 8);
        o[1] = (uint8_t) (st[x] >> 16);
        o[2] = (uint8_t) (st[x] >> 24);
      }
      break;
    default:
      for (uint32_t x = 0; x < n; x++, o += 2) {
        o[0] = (uint8_t) (st[x] >> 16);
        o[1] = (uint8_t) (st[x] >> 24);
      }
      break;
  }
  return o;
}

// Frames i to i + n - 1 of block k, all in the block
static uint8_t *emit(bpc1_walk_t *w, uint8_t *out, uint32_t k, uint32_t i, uint32_t n) {
  uint32_t st[STAGE * BPC1_MAX_CH];
  const uint32_t nch = w->nch;
  while (n > 0) {
    uint32_t m = min32(n, STAGE);
    uint32_t f = k * w->fpb + i;  // in the regular file
    if (w->dop) {
      gen_dop(w, st, k, i, m);
      for (uint32_t x = 0; x < m; x++) {
        uint32_t marker = ((f + x) & 1u) ? 0xFA000000u : 0x05000000u;
        for (uint32_t c = 0; c < nch; c++) {
          st[x * nch + c] |= marker;
        }
      }
    } else {
      gen_pcm(w, st, k, i, m);
    }
    if (w->file.fault == BPC1_FAULT_ALTERED) {
      alter(w, st, k, f, m);
    }
    out = pack(st, m * nch, w->slot, out);
    i += m;
    n -= m;
  }
  return out;
}

uint32_t bpc1_walk_fill(bpc1_walk_t *w, uint8_t *out, uint32_t n) {
  const uint32_t fb = bpc1_walk_frame_bytes(w);
  const bool incomplete = w->file.fault == BPC1_FAULT_INCOMPLETE;
  uint32_t done = 0;
  while (done < n) {
    uint32_t run;
    if (w->zeros > 0) {
      run = min32(n - done, w->zeros);
      memset(out, 0, run * fb);
      out += run * fb;
      w->zeros -= run;
      done += run;
      continue;
    }
    if (incomplete && w->pos == w->file.at) {
      w->pos += w->file.len;  // the frames a negative file leaves out
    }
    if (w->pos >= w->end) {
      break;
    }
    uint32_t k = w->pos / w->fpb, i = w->pos - k * w->fpb;
    if (!w->blk_ok || w->blk.k != k) {
      bpc1_block_init(&w->sig, k, flags_of(w, k), true, (uint16_t) w->total, &w->blk);
      w->blk_ok = true;
    }
    run = min32(min32(n - done, w->fpb - i), w->end - w->pos);
    if (incomplete && w->pos < w->file.at) {
      run = min32(run, w->file.at - w->pos);
    }
    out = emit(w, out, k, i, run);
    w->pos += run;
    done += run;
  }
  return done;
}
