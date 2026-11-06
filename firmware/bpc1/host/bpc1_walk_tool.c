// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_bpc1_walk.py: the frame walker of the BPC1
// generator on the host. Not part of the firmware.
//
//   lookup                       numbers on stdin, one per line; per line
//                                "-" or: kind depth_code rate_code lead body
//                                tail fault part cut at len rate frames
//   info <file> <slot>           rate=<Hz> frames=<n> frame_bytes=<n>
//   fill <file> <slot> [<split>] [head=<frames>]
//                                the frames' bytes on stdout, from one call,
//                                or in pieces: <split> is a list of sizes
//                                repeated (5,6,7) or rand:<seed> (1 to 300)
//   ref <file> <slot>            the same bytes, frame by frame from
//                                bpc1_pcm_unit or bpc1_dsd_unit (a regular
//                                file only)
//   bench <file> <slot> <frames per fill> <seconds>
//                                ns_per_frame=<x> frames=<n>
//
// <file> is a five-digit number of the table or a layout
// <kind>:<depth_code>:<rate_code>:<lead>:<body>:<tail>. An error exits 1.

#define _POSIX_C_SOURCE 200809L

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "bpc1_walk.h"

static const char *const fault_names[] = {"none", "altered", "incomplete", "gap"};

static int fail(const char *what) {
  fprintf(stderr, "bpc1_walk_tool: %s\n", what);
  return 1;
}

static bool parse_u32(const char *s, uint32_t *v) {
  char *end;
  unsigned long x = strtoul(s, &end, 10);
  if (*s == '\0' || *end != '\0' || x > 0xFFFFFFFFul) {
    return false;
  }
  *v = (uint32_t) x;
  return true;
}

// A number (five digits) or a layout
static bool file_of(const char *s, bpc1_file_t *f) {
  uint32_t v[6];
  if (strchr(s, ':') == NULL) {
    return strlen(s) == 5 && parse_u32(s, &v[0]) && bpc1_walk_lookup(v[0], f);
  }
  char buf[64];
  if (strlen(s) >= sizeof(buf)) {
    return false;
  }
  strcpy(buf, s);
  int n = 0;
  for (char *t = strtok(buf, ":"); t != NULL; t = strtok(NULL, ":")) {
    if (n == 6 || !parse_u32(t, &v[n]) || v[n] > 0xFFFFu) {
      return false;
    }
    n++;
  }
  if (n != 6) {
    return false;
  }
  memset(f, 0, sizeof(*f));
  f->kind = (uint8_t) v[0];
  f->depth_code = (uint8_t) v[1];
  f->rate_code = (uint8_t) v[2];
  f->lead = (uint16_t) v[3];
  f->body = (uint16_t) v[4];
  f->tail = (uint16_t) v[5];
  return v[0] < 256 && v[1] < 256 && v[2] < 256;
}

static bool start(bpc1_walk_t *w, const char *file, const char *slot) {
  bpc1_file_t f;
  uint32_t s;
  return file_of(file, &f) && parse_u32(slot, &s) && s < 256 && bpc1_walk_init(w, &f, (uint8_t) s);
}

static int lookup(void) {
  char line[64];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    line[strcspn(line, "\r\n")] = '\0';
    bpc1_file_t f;
    bpc1_walk_t w;
    if (!file_of(line, &f) || strchr(line, ':') != NULL || !bpc1_walk_init(&w, &f, 4)) {
      printf("-\n");
      continue;
    }
    printf("%u %u %u %u %u %u %s %u %" PRIu32 " %" PRIu32 " %" PRIu32 " %" PRIu32 " %" PRIu32 "\n", f.kind,
           f.depth_code, f.rate_code, f.lead, f.body, f.tail, fault_names[f.fault], f.part, f.cut, f.at, f.len,
           bpc1_walk_rate(&w), bpc1_walk_frames(&w));
  }
  return 0;
}

// Piece sizes: a list repeated, or seeded random sizes from 1 to 300
typedef struct {
  uint32_t sizes[16];
  uint32_t n, next;
  uint64_t rng;
  bool random;
} split_t;

static bool split_parse(const char *s, split_t *sp) {
  memset(sp, 0, sizeof(*sp));
  if (strncmp(s, "rand:", 5) == 0) {
    uint32_t seed;
    sp->random = true;
    sp->rng = parse_u32(s + 5, &seed) ? 0x9E3779B97F4A7C15u * (seed + 1u) : 0;
    return sp->rng != 0;
  }
  char buf[128];
  if (strlen(s) >= sizeof(buf)) {
    return false;
  }
  strcpy(buf, s);
  for (char *t = strtok(buf, ","); t != NULL; t = strtok(NULL, ",")) {
    if (sp->n == 16 || !parse_u32(t, &sp->sizes[sp->n]) || sp->sizes[sp->n] == 0) {
      return false;
    }
    sp->n++;
  }
  return sp->n > 0;
}

static uint32_t split_next(split_t *sp) {
  if (sp->random) {
    sp->rng ^= sp->rng << 13;
    sp->rng ^= sp->rng >> 7;
    sp->rng ^= sp->rng << 17;
    return 1u + (uint32_t) (sp->rng % 300u);
  }
  uint32_t v = sp->sizes[sp->next];
  sp->next = (sp->next + 1u) % sp->n;
  return v;
}

static int fill(int argc, char **argv) {
  bpc1_walk_t w;
  if (!start(&w, argv[2], argv[3])) {
    return fail("no such file or slot");
  }
  split_t sp;
  bool split = false;
  uint32_t head = 0xFFFFFFFFu;
  for (int a = 4; a < argc; a++) {
    if (strncmp(argv[a], "head=", 5) == 0) {
      if (!parse_u32(argv[a] + 5, &head)) {
        return fail("bad head=");
      }
    } else if (!split && split_parse(argv[a], &sp)) {
      split = true;
    } else {
      return fail("bad split");
    }
  }
  const uint32_t fb = bpc1_walk_frame_bytes(&w);
  static uint8_t buf[4096 * 8];
  uint32_t left = head;
  for (;;) {
    uint32_t n = split ? split_next(&sp) : (uint32_t) (sizeof(buf) / fb);
    if (n > sizeof(buf) / fb) {
      n = sizeof(buf) / fb;
    }
    if (n > left) {
      n = left;
    }
    uint32_t got = bpc1_walk_fill(&w, buf, n);
    if (got == 0) {
      break;
    }
    fwrite(buf, fb, got, stdout);
    left -= got;
  }
  return 0;
}

// The reference: bpc1_pcm_unit or bpc1_dsd_unit, frame by frame
static int ref(char **argv) {
  bpc1_walk_t w;
  if (!start(&w, argv[2], argv[3]) || w.file.part != 0 || w.file.fault != BPC1_FAULT_NONE) {
    return fail("no such regular file or slot");
  }
  const bpc1_signal_t *s = &w.sig;
  const uint32_t lead = w.file.lead, body = w.file.body, slot = w.slot;
  bpc1_block_t b;
  for (uint32_t k = 0; k < w.total; k++) {
    uint8_t flags = (uint8_t) ((k < lead ? BPC1_F_LEAD_IN : 0) | (k >= lead + body ? BPC1_F_TAIL : 0)
                               | (k + 1 == w.total ? BPC1_F_LAST : 0));
    bpc1_block_init(s, k, flags, true, (uint16_t) w.total, &b);
    for (uint32_t i = 0; i < w.fpb; i++) {
      for (uint32_t c = 0; c < w.nch; c++) {
        uint32_t word;
        if (w.dop) {
          uint32_t marker = ((k * w.fpb + i) & 1u) ? 0xFAu : 0x05u;
          word = ((marker << 16) | ((uint32_t) bpc1_dsd_unit(s, &b, c, 2 * i) << 8)
                  | bpc1_dsd_unit(s, &b, c, 2 * i + 1)) << 8;
        } else {
          word = bpc1_pcm_unit(s, &b, c, i);
        }
        uint8_t o[4] = {(uint8_t) word, (uint8_t) (word >> 8), (uint8_t) (word >> 16), (uint8_t) (word >> 24)};
        fwrite(o + 4 - slot, 1, slot, stdout);
      }
    }
  }
  return 0;
}

static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double) t.tv_sec + 1e-9 * (double) t.tv_nsec;
}

// Fills the file over and over, per fill a packet's frames, for at least
// the given seconds; the bytes are not used, so a checksum keeps them.
static int bench(char **argv) {
  bpc1_walk_t w;
  uint32_t per, seconds;
  if (!start(&w, argv[2], argv[3]) || !parse_u32(argv[4], &per) || per == 0 || per > 1024
      || !parse_u32(argv[5], &seconds)) {
    return fail("bad bench arguments");
  }
  static uint8_t buf[1024 * 8];
  const bpc1_walk_t first = w;
  uint64_t frames = 0;
  uint32_t sum = 0;
  double t0 = now(), t;
  do {
    for (int r = 0; r < 1000; r++) {
      uint32_t got = bpc1_walk_fill(&w, buf, per);
      if (got < per) {
        w = first;
      }
      frames += got;
      sum += buf[got % 7];
    }
    t = now();
  } while (t - t0 < seconds);
  printf("ns_per_frame=%.2f frames=%" PRIu64 " sum=%" PRIu32 "\n", 1e9 * (t - t0) / (double) frames, frames,
         sum);
  return 0;
}

int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "lookup") == 0) {
    return lookup();
  }
  if (argc == 4 && strcmp(argv[1], "info") == 0) {
    bpc1_walk_t w;
    if (!start(&w, argv[2], argv[3])) {
      return fail("no such file or slot");
    }
    printf("rate=%" PRIu32 " frames=%" PRIu32 " frame_bytes=%" PRIu32 "\n", bpc1_walk_rate(&w),
           bpc1_walk_frames(&w), bpc1_walk_frame_bytes(&w));
    return 0;
  }
  if (argc >= 4 && argc <= 6 && strcmp(argv[1], "fill") == 0) {
    return fill(argc, argv);
  }
  if (argc == 4 && strcmp(argv[1], "ref") == 0) {
    return ref(argv);
  }
  if (argc == 6 && strcmp(argv[1], "bench") == 0) {
    return bench(argv);
  }
  return fail("usage: lookup | info <file> <slot> | fill <file> <slot> [<split>] [head=<n>] | "
              "ref <file> <slot> | bench <file> <slot> <frames per fill> <seconds>");
}
