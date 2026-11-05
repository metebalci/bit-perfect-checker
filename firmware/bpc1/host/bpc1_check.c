// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_bpc1_checker.py: runs the firmware's BPC1 checker
// engine on a raw capture. Not part of the firmware.
//
//   bpc1_check [-t] [-s] [-o <frame>]... <host rate> <slot bits> <chunk frames> [file]
//
// The input (file or stdin) is raw s32le: 2 channels interleaved, samples
// left-justified in 32 bits. It is fed in chunks of <chunk frames>, with the
// bits below <slot bits> cleared, as the device receives them. Prints
// two lines per result, in the order of the stream: the fields of the
// firmware's result line that the engine knows,
//   check file=<n, from 1> verdict=<v> kind=<k> dop=<0/1> seed=... dur=<s>
// then the engine's diagnostics,
//   diag file=<n> why=<check> diff_unit=<n> body_k=<n> compared=<n> ...
// and at the end
//   stream outside=<frames> silent=<frames> results=<n>
// -t adds the engine's time per frame on stderr; -s allows silence outside
// files; -o <frame> reports a device FIFO overrun before that frame.

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "bpc1_checker.h"

static bpc1_checker_t ck;
static uint32_t buf[1u << 20];

static double now_s(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (double) ts.tv_sec + (double) ts.tv_nsec * 1e-9;
}

// The fields of the firmware's result line that the engine knows, in its
// order and with its names; 0 for unknown values.
static void print(const bpc1_report_t *r, unsigned n) {
  bool file = r->kind != 0;
  char diff[16];
  bpc1_diff_text(r, diff, sizeof(diff));
  printf("check file=%u verdict=%s kind=%s dop=%d seed=0x%08" PRIX32 " nch=%u file_rate=%" PRIu32 " depth=%u", n,
         bpc1_verdict_name(r->verdict), bpc1_kind_name(r->kind), r->dop ? 1 : 0, file ? r->id.seed : 0,
         file ? r->id.nch : 0, r->rate, r->depth);
  uint64_t ms = r->host_rate ? r->frames * 1000u / r->host_rate : 0;
  printf(" rate=%" PRIu32 " slot=%u bits=%u end=%s diff=%s dur=%" PRIu64 ".%03" PRIu64 "\n", r->host_rate,
         r->slot_bits, r->bits, bpc1_end_name(r->end), diff, ms / 1000u, ms % 1000u);
  printf("diag file=%u why=%s diff_unit=%" PRIu64 " body_k=%" PRId32 " compared=%" PRIu64 " first_frame=%" PRIu64
         " frames=%" PRIu64 " anchors=%" PRIu32 "\n",
         n, bpc1_why_name(r->why), r->diff_known ? r->diff_unit : 0, r->body_k, r->compared, r->first_frame,
         r->frames, r->anchors);
}

int main(int argc, char **argv) {
  int a = 1;
  int timing = 0;
  bool silence = false;
  uint64_t over[16];
  unsigned nover = 0, io = 0;
  while (a < argc && argv[a][0] == '-' && argv[a][1]) {
    if (strcmp(argv[a], "-t") == 0) {
      timing = 1;
      a++;
    } else if (strcmp(argv[a], "-s") == 0) {
      silence = true;
      a++;
    } else if (strcmp(argv[a], "-o") == 0 && a + 1 < argc && nover < 16) {
      over[nover++] = strtoull(argv[a + 1], NULL, 0);
      a += 2;
    } else {
      break;
    }
  }
  if (argc - a < 3) {
    fprintf(stderr, "usage: bpc1_check [-t] [-s] [-o frame]... <host rate> <slot bits> <chunk frames> [file]\n");
    return 2;
  }
  uint32_t rate = (uint32_t) strtoul(argv[a], NULL, 0);
  uint8_t slot = (uint8_t) strtoul(argv[a + 1], NULL, 0);
  uint32_t chunk = (uint32_t) strtoul(argv[a + 2], NULL, 0);
  if (chunk == 0 || chunk > sizeof(buf) / sizeof(buf[0]) / BPC1_MAX_CH) {
    fprintf(stderr, "bad chunk size\n");
    return 2;
  }
  FILE *in = stdin;
  if (argc - a > 3 && strcmp(argv[a + 3], "-") != 0) {
    in = fopen(argv[a + 3], "rb");
    if (!in) {
      perror(argv[a + 3]);
      return 2;
    }
  }

  bpc1_checker_init(&ck, rate, slot);
  bpc1_checker_allow_silence(&ck, silence);
  double spent = 0;
  uint64_t frames = 0;
  unsigned results = 0;
  bpc1_report_t r;
  for (;;) {
    uint32_t want = chunk;
    if (io < nover && over[io] >= frames && over[io] - frames < want) {
      want = (uint32_t) (over[io] - frames);  // stop at the overrun
    }
    size_t got = want ? fread(buf, sizeof(uint32_t) * BPC1_MAX_CH, want, in) : 0;
    bool at_overrun = io < nover && over[io] == frames + got;
    if (got == 0 && want != 0) {
      break;
    }
    if (slot < 32) {
      uint32_t keep = 0xFFFFFFFFu << (32u - slot);
      for (size_t i = 0; i < BPC1_MAX_CH * got; i++) {
        buf[i] &= keep;
      }
    }
    double t0 = now_s();
    bpc1_checker_feed(&ck, buf, (uint32_t) got);
    spent += now_s() - t0;
    frames += got;
    if (at_overrun) {
      bpc1_checker_overrun(&ck);  // after the frames before it
      io++;
    }
    while (bpc1_checker_pop(&ck, &r)) {
      print(&r, ++results);
    }
  }
  double t0 = now_s();
  bpc1_checker_end(&ck);
  spent += now_s() - t0;
  while (bpc1_checker_pop(&ck, &r)) {
    print(&r, ++results);
  }
  printf("stream outside=%" PRIu64 " silent=%" PRIu64 " results=%u\n", bpc1_checker_outside(&ck),
         bpc1_checker_silent(&ck), results);
  if (timing) {
    fprintf(stderr, "frames=%" PRIu64 " ns_per_frame=%.1f sizeof=%zu\n", frames,
            frames ? spent * 1e9 / (double) frames : 0.0, sizeof(ck));
  }
  if (in != stdin) {
    fclose(in);
  }
  return 0;
}
