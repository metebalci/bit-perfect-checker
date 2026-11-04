// BPC1 v1 primitives and expected signal, from docs/bpc1.md.

#include "bpc1.h"

static const uint32_t rates[] = {
  44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000,
  2822400, 5644800, 11289600,
};
#define N_RATES (sizeof(rates) / sizeof(rates[0]))

static const int8_t barker13[13] = {1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1};

//--------------------------------------------------------------------+
// Primitives (section 4)
//--------------------------------------------------------------------+

uint32_t bpc1_lb32(uint32_t key, uint32_t n) {
  uint32_t x = n ^ key;
  x ^= x >> 16;
  x *= 0x7FEB352Du;
  x ^= x >> 15;
  x *= 0x846CA68Bu;
  x ^= x >> 16;
  return x;
}

uint64_t bpc1_splitmix64_first(uint64_t v) {
  uint64_t z = v + 0x9E3779B97F4A7C15u;
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9u;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBu;
  return z ^ (z >> 31);
}

uint16_t bpc1_crc16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t) (data[i] << 8);
    for (int b = 0; b < 8; b++) {
      crc = (crc & 0x8000u) ? (uint16_t) ((crc << 1) ^ 0x1021u) : (uint16_t) (crc << 1);
    }
  }
  return crc;
}

uint16_t bpc1_crc16_words(const uint16_t *words, size_t n) {
  uint8_t bytes[16];  // at most 8 words
  for (size_t i = 0; i < n && i < 8; i++) {
    bytes[2 * i] = (uint8_t) (words[i] >> 8);
    bytes[2 * i + 1] = (uint8_t) words[i];
  }
  return bpc1_crc16(bytes, 2 * (n < 8 ? n : 8));
}

//--------------------------------------------------------------------+
// Header words (section 6)
//--------------------------------------------------------------------+

bool bpc1_fields_parse(const uint16_t h[5], bpc1_fields_t *f) {
  f->version = (uint8_t) (h[0] >> 12);
  f->kind = (uint8_t) ((h[0] >> 8) & 15u);
  f->depth_code = (uint8_t) ((h[0] >> 4) & 15u);
  f->rate_code = (uint8_t) (h[0] & 15u);
  f->channel = (uint8_t) (h[1] >> 12);
  f->nch = (uint8_t) ((h[1] >> 8) & 15u);
  f->flags = (uint8_t) h[1];
  f->seed = ((uint32_t) h[2] << 16) | h[3];
  f->block = h[4];

  bool pcm = !bpc1_kind_dsd(f->kind);
  bool kind = f->kind == BPC1_KIND_PCM || f->kind == BPC1_KIND_DSD || f->kind == BPC1_KIND_SWEEP;
  return f->version == BPC1_VERSION && kind && f->depth_code >= 1
         && f->depth_code <= 4 && f->rate_code < N_RATES && f->nch >= 1 && f->channel < f->nch
         && pcm == (f->depth_code != 1) && pcm == (f->rate_code < 10)
         && (f->kind != BPC1_KIND_SWEEP || f->depth_code == 2 || f->depth_code == 3);  // SWEEP: 16 or 24 bits
}

bool bpc1_sync_parse(const uint16_t w[8], bpc1_fields_t *f) {
  return w[0] == BPC1_MAGIC0 && w[1] == BPC1_MAGIC1 && bpc1_crc16_words(w, 7) == w[7]
         && bpc1_fields_parse(&w[2], f);
}

bool bpc1_rhdr_parse(const uint16_t r[7], bpc1_fields_t *f, uint16_t *total) {
  if (bpc1_crc16_words(r, 6) != r[6] || !bpc1_fields_parse(r, f)) {
    return false;
  }
  *total = r[5];
  return true;
}

bool bpc1_same_file(const bpc1_fields_t *a, const bpc1_fields_t *b) {
  return a->kind == b->kind && a->depth_code == b->depth_code && a->rate_code == b->rate_code
         && a->nch == b->nch && a->seed == b->seed;
}

//--------------------------------------------------------------------+
// Signal (sections 3, 5, 7, 8)
//--------------------------------------------------------------------+

uint32_t bpc1_rate(uint8_t rate_code) {
  return rate_code < N_RATES ? rates[rate_code] : 0;
}

bool bpc1_signal_init(bpc1_signal_t *s, const bpc1_fields_t *f) {
  if (f->nch > BPC1_MAX_CH) {
    return false;
  }
  static const uint8_t depths[5] = {0, 1, 16, 24, 32};
  static const uint8_t active[5] = {0, 0, 10, 16, 24};
  s->kind = f->kind;
  s->depth_code = f->depth_code;
  s->rate_code = f->rate_code;
  s->nch = f->nch;
  s->seed = f->seed;
  s->depth = depths[f->depth_code];
  s->active = active[f->depth_code];
  s->dsd = bpc1_kind_dsd(f->kind);
  s->sweep = f->kind == BPC1_KIND_SWEEP;
  s->rate = rates[f->rate_code];
  uint32_t base = (s->rate % 44100u == 0) ? 44100u : 48000u;
  s->mult = s->rate / base;
  s->block_len = 4096u * s->mult;
  s->units = s->dsd ? s->block_len / 8u : s->block_len;
  s->chip = (base == 44100u ? 11u : 12u) * s->mult;
  s->r_start = 512u * s->mult;
  for (uint32_t c = 0; c < s->nch; c++) {
    uint64_t v = ((uint64_t) s->seed << 32) | ((uint64_t) s->kind << 24) | ((uint64_t) s->rate_code << 16)
                 | ((uint64_t) s->depth_code << 8) | c;
    s->keys[c] = (uint32_t) bpc1_splitmix64_first(v);
    s->sweep_keys[c] = s->sweep ? bpc1_sweep_key(s->keys[c], s->depth) : 0;
  }
  return true;
}

uint32_t bpc1_sweep_key(uint32_t channel_key, uint32_t depth) {
  return channel_key & ((1u << depth) - 1u);  // the low D bits of the channel key
}

void bpc1_block_init(const bpc1_signal_t *s, uint32_t k, uint8_t flags, bool total_known,
                     uint16_t total, bpc1_block_t *b) {
  b->k = k;
  b->flags = flags;
  b->total_known = total_known;
  b->total = total;
  for (uint32_t c = 0; c < s->nch; c++) {
    uint16_t h[5] = {
      (uint16_t) ((BPC1_VERSION << 12) | ((uint32_t) s->kind << 8) | ((uint32_t) s->depth_code << 4)
                  | s->rate_code),
      (uint16_t) ((c << 12) | ((uint32_t) s->nch << 8) | flags),
      (uint16_t) (s->seed >> 16),
      (uint16_t) s->seed,
      (uint16_t) k,
    };
    b->sync[c][0] = BPC1_MAGIC0;
    b->sync[c][1] = BPC1_MAGIC1;
    for (int i = 0; i < 5; i++) {
      b->sync[c][2 + i] = h[i];
      b->rhdr[c][i] = h[i];
    }
    b->sync[c][7] = bpc1_crc16_words(b->sync[c], 7);
    b->rhdr[c][5] = total;
    b->rhdr[c][6] = bpc1_crc16_words(b->rhdr[c], 6);
  }
}

int bpc1_chip(const uint16_t r[7], uint32_t q) {
  if (q < 13) {
    return barker13[q];
  }
  uint32_t j = q - 13;
  uint32_t bit = (r[j / 32] >> (15 - (j % 32) / 2)) & 1u;
  int v = bit ? 1 : -1;  // Manchester: a 1 is (+1, -1), a 0 is (-1, +1)
  return (j & 1u) ? -v : v;
}

// The first chip that depends on the total block count (r5)
#define CHIP_R5 (13u + 5u * 32u)

static uint32_t walk(uint32_t depth, uint32_t c, uint32_t j) {
  uint32_t p = (c & 1u) ? 63u - j : j;
  uint32_t mask = depth == 32 ? 0xFFFFFFFFu : (1u << depth) - 1u;
  uint32_t alt = 0x55555555u & mask;
  if (p < depth) {
    return 1u << p;
  }
  if (p < 2 * depth) {
    return mask ^ (1u << (p - depth));
  }
  return (p & 1u) ? mask ^ alt : alt;
}

uint32_t bpc1_pcm_unit(const bpc1_signal_t *s, const bpc1_block_t *b, uint32_t c, uint32_t i) {
  uint32_t shift = 32u - s->depth;
  if (i < BPC1_SYNC_FRAMES) {
    uint32_t h = bpc1_lb32(s->keys[c], b->k * s->block_len + i);
    uint32_t low = shift == 0 ? 0xFFFFFFFFu : ~((1u << shift) - 1u);
    return (((uint32_t) b->sync[c][i] << 16) | (h >> 16)) & low;
  }
  if (i < BPC1_BURST_END) {
    return walk(s->depth, c, i - BPC1_SYNC_FRAMES) << shift;
  }
  if (bpc1_is_rhdr(b->k)) {
    if (i >= s->r_start) {
      uint32_t q = (i - s->r_start) / s->chip;
      if (q < BPC1_N_CHIPS) {
        if (!b->total_known && q >= CHIP_R5) {
          return 0x20000000u;
        }
        return bpc1_chip(b->rhdr[c], q) > 0 ? 0x20000000u : 0xE0000000u;
      }
    }
    return 0;
  }
  if (s->sweep) {
    return bpc1_lbd(s->depth, s->sweep_keys[c], bpc1_sweep_index(b->k, s->block_len, i)) << shift;
  }
  uint32_t h = bpc1_lb32(s->keys[c], b->k * s->block_len + i);
  return (uint32_t) ((int32_t) h >> (32u - s->active)) << shift;
}

uint8_t bpc1_dsd_unit(const bpc1_signal_t *s, const bpc1_block_t *b, uint32_t c, uint32_t i) {
  if (i < BPC1_SYNC_BYTES) {
    uint16_t w = b->sync[c][i / 2];
    return (uint8_t) ((i & 1u) ? w : w >> 8);
  }
  if (bpc1_is_rhdr(b->k)) {
    uint32_t a = s->r_start / 8u;
    if (i >= a) {
      uint32_t q = (i - a) / (s->chip / 8u);
      if (q < BPC1_N_CHIPS) {
        if (!b->total_known && q >= CHIP_R5) {
          return 0xEE;
        }
        return bpc1_chip(b->rhdr[c], q) > 0 ? 0xEE : 0x11;  // 1110 or 0001 patterns
      }
    }
    return 0x69;  // DSD idle
  }
  // Each hash bit as a pair of opposite bits, earliest bit as MSB
  uint32_t m = b->k * s->block_len + 8u * i;
  uint32_t h = bpc1_lb32(s->keys[c], m >> 6);
  uint32_t first = (m >> 1) & 31u;
  uint8_t out = 0;
  for (uint32_t j = 0; j < 4; j++) {
    uint32_t x = (h >> (31u - (first + j))) & 1u;
    out |= (uint8_t) (((x << 1) | (x ^ 1u)) << (6u - 2u * j));
  }
  return out;
}
