// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// BPC1 v1, the test signal of docs/bpc1.md: primitives, header
// words, and the expected signal unit by unit. Portable C99, no hardware:
// the firmware checker builds on it, and tests compile it on the host.
//
// Units are PCM frames, or bytes per channel (8 DSD bits, earliest bit as
// MSB) for DSD. A PCM unit's value is the 32-bit word W: the sample
// left-justified, bits below the depth zero (spec section 1).

#ifndef BPC1_H
#define BPC1_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef BPC1_MAX_CH
#define BPC1_MAX_CH 2  // the spec allows 15; a stereo device needs 2
#endif

#define BPC1_MAGIC0     0xB1C0u
#define BPC1_MAGIC1     0x5EEDu
#define BPC1_VERSION    1u

#define BPC1_KIND_PCM   1u
#define BPC1_KIND_DSD   2u  // DSD, in DSF, DFF or DoP alike
#define BPC1_KIND_SWEEP 5u  // PCM code sweep: PCM but for its payload; 0, 3, 4, 6-15 reserved

#define BPC1_F_LEAD_IN  0x01u
#define BPC1_F_TAIL     0x02u
#define BPC1_F_LAST     0x04u

#define BPC1_N_CHIPS    (13u + 7u * 32u)
#define BPC1_SYNC_FRAMES 8u    // PCM X-sync: 8 frames
#define BPC1_SYNC_BYTES  16u   // DSD X-sync: 16 bytes
#define BPC1_BURST_END   72u   // PCM: X-sync and walking-bit burst

// Primitives (section 4)
uint32_t bpc1_lb32(uint32_t key, uint32_t n);
uint64_t bpc1_splitmix64_first(uint64_t v);
uint16_t bpc1_crc16(const uint8_t *data, size_t len);
uint16_t bpc1_crc16_words(const uint16_t *words, size_t n);

// The header fields of an X-sync or R-header (section 6)
typedef struct {
  uint8_t version;
  uint8_t kind;
  uint8_t depth_code;
  uint8_t rate_code;
  uint8_t channel;
  uint8_t nch;
  uint8_t flags;
  uint32_t seed;
  uint16_t block;
} bpc1_fields_t;

// h0..h4 -> fields; false if a code is reserved or the fields disagree.
bool bpc1_fields_parse(const uint16_t h[5], bpc1_fields_t *f);
// Eight X-sync words w0..w7: magic, CRC and fields.
bool bpc1_sync_parse(const uint16_t w[8], bpc1_fields_t *f);
// Seven R-header words r0..r6: CRC and fields, and the total block count.
bool bpc1_rhdr_parse(const uint16_t r[7], bpc1_fields_t *f, uint16_t *total);
// Same file: kind, depth, rate, channels, seed.
bool bpc1_same_file(const bpc1_fields_t *a, const bpc1_fields_t *b);

// One file's signal (sections 3 to 8)
typedef struct {
  uint8_t kind;
  uint8_t depth_code;
  uint8_t rate_code;
  uint8_t nch;
  uint32_t seed;
  uint8_t depth;       // D: 1 for DSD, else 16, 24 or 32
  uint8_t active;      // A: payload bits (PCM)
  bool dsd;
  bool sweep;          // kind SWEEP: the payload is the code sweep
  uint32_t rate;       // frames or DSD bits per second
  uint32_t mult;       // M
  uint32_t block_len;  // B, in frames or DSD bits
  uint32_t units;      // units per block: B frames, or B / 8 bytes
  uint32_t chip;       // L, in frames or DSD bits
  uint32_t r_start;    // 512 M
  uint32_t keys[BPC1_MAX_CH];
  uint32_t sweep_keys[BPC1_MAX_CH];  // SWEEP: the lbD key of each channel
} bpc1_signal_t;

// False if f has more channels than BPC1_MAX_CH.
bool bpc1_signal_init(bpc1_signal_t *s, const bpc1_fields_t *f);
// The DSD kind; PCM and SWEEP are PCM.
static inline bool bpc1_kind_dsd(uint8_t kind) {
  return kind == BPC1_KIND_DSD;
}

// The code sweep (kind SWEEP): lbD, a bijection on D bits (D = 16 or 24),
// the lbD key of a channel from its channel key, and the sweep index of
// payload frame i of block k (B frames per block).
static inline uint32_t bpc1_lbd(uint32_t depth, uint32_t key, uint32_t s) {
  uint32_t m = (1u << depth) - 1u, h = depth / 2u;
  uint32_t x = (s ^ key) & m;
  x ^= x >> h;
  x = (x * (0x7FEB352Du & m)) & m;
  x ^= x >> (h - 1u);
  x = (x * (0x846CA68Bu & m)) & m;
  x ^= x >> h;
  return x;
}
uint32_t bpc1_sweep_key(uint32_t channel_key, uint32_t depth);
static inline uint32_t bpc1_sweep_index(uint32_t k, uint32_t block_len, uint32_t i) {
  // the payload frames of the blocks before k, R-header blocks (k mod 16 = 8) left out
  return (k - (k + 7u) / 16u) * (block_len - BPC1_BURST_END) + i - BPC1_BURST_END;
}
uint32_t bpc1_rate(uint8_t rate_code);
static inline bool bpc1_is_rhdr(uint32_t k) {
  return (k & 15u) == 8u;
}

// One block, with what its expected units depend on: the flags (which the
// receiver reads from the block's X-sync) and, for R-header blocks, the
// file's total block count.
typedef struct {
  uint32_t k;
  uint8_t flags;
  bool total_known;
  uint16_t total;
  uint16_t sync[BPC1_MAX_CH][8];
  uint16_t rhdr[BPC1_MAX_CH][7];
} bpc1_block_t;

void bpc1_block_init(const bpc1_signal_t *s, uint32_t k, uint8_t flags, bool total_known,
                     uint16_t total, bpc1_block_t *b);

// Chip q (0 to 236) of the R-header words r: +1 or -1.
int bpc1_chip(const uint16_t r[7], uint32_t q);

// The expected unit at offset i of block b for source channel c: the PCM
// word W, or the DSD byte. In an R-header block whose total is not known,
// the chips of r5 and r6 are unknown; bpc1_pcm_unit() returns +2^29 there.
uint32_t bpc1_pcm_unit(const bpc1_signal_t *s, const bpc1_block_t *b, uint32_t c, uint32_t i);
uint8_t bpc1_dsd_unit(const bpc1_signal_t *s, const bpc1_block_t *b, uint32_t c, uint32_t i);
static inline uint32_t bpc1_unit(const bpc1_signal_t *s, const bpc1_block_t *b, uint32_t c, uint32_t i) {
  return s->dsd ? bpc1_dsd_unit(s, b, c, i) : bpc1_pcm_unit(s, b, c, i);
}

#endif
