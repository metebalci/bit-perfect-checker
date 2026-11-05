// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_bpc1_c.py: answers queries about the expected
// BPC1 signal with the firmware's C code, one query per line on stdin.
// Not part of the firmware.
//
//   lb32 <key> <n>                  -> hex
//   sm64 <v>                        -> hex
//   crc <hex bytes>                 -> hex
//   sig <kind> <depth_code> <rate_code> <nch> <seed> <lead> <body> <total>
//                                   -> "ok", sets the signal for the queries below
//   key <c>                         -> hex
//   sync <k>                        -> one line per channel, 8 hex words
//   pcm <first frame> <count>       -> one line per frame: samples at the file depth
//   dsd <first bit> <bytes>         -> one line per channel: hex bytes
//   parse <h0> <h1>                 -> 1 if header words h0, h1 (seed and
//                                      block 0) are valid, else 0
//
// Numbers may be decimal or 0x-hex. Flags and the total block count follow
// the file layout of the sig line, as a generator writes them.

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bpc1.h"

static bpc1_signal_t sig;
static uint32_t lead, body, total;

static uint8_t flags_of(uint32_t k) {
  uint8_t f = 0;
  if (k < lead) {
    f |= BPC1_F_LEAD_IN;
  }
  if (k >= lead + body) {
    f |= BPC1_F_TAIL;
  }
  if (k + 1 == total) {
    f |= BPC1_F_LAST;
  }
  return f;
}

static void block(uint32_t k, bpc1_block_t *b) {
  bpc1_block_init(&sig, k, flags_of(k), true, (uint16_t) total, b);
}

static uint64_t num(const char *s) {
  return strtoull(s, NULL, 0);
}

int main(void) {
  char line[4096];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    char *argv[16];
    int argc = 0;
    for (char *t = strtok(line, " \t\r\n"); t != NULL && argc < 16; t = strtok(NULL, " \t\r\n")) {
      argv[argc++] = t;
    }
    if (argc == 0) {
      continue;
    }
    const char *cmd = argv[0];

    if (strcmp(cmd, "lb32") == 0 && argc == 3) {
      printf("0x%08" PRIX32 "\n", bpc1_lb32((uint32_t) num(argv[1]), (uint32_t) num(argv[2])));
    } else if (strcmp(cmd, "sm64") == 0 && argc == 2) {
      printf("0x%016" PRIX64 "\n", bpc1_splitmix64_first(num(argv[1])));
    } else if (strcmp(cmd, "crc") == 0 && argc == 2) {
      uint8_t bytes[256];
      size_t n = strlen(argv[1]) / 2;
      for (size_t i = 0; i < n && i < sizeof(bytes); i++) {
        char hex[3] = {argv[1][2 * i], argv[1][2 * i + 1], 0};
        bytes[i] = (uint8_t) strtoul(hex, NULL, 16);
      }
      printf("0x%04X\n", bpc1_crc16(bytes, n));
    } else if (strcmp(cmd, "sig") == 0 && argc == 9) {
      bpc1_fields_t f = {
        .version = 1,
        .kind = (uint8_t) num(argv[1]),
        .depth_code = (uint8_t) num(argv[2]),
        .rate_code = (uint8_t) num(argv[3]),
        .nch = (uint8_t) num(argv[4]),
        .seed = (uint32_t) num(argv[5]),
      };
      lead = (uint32_t) num(argv[6]);
      body = (uint32_t) num(argv[7]);
      total = (uint32_t) num(argv[8]);
      printf(bpc1_signal_init(&sig, &f) ? "ok\n" : "error\n");
    } else if (strcmp(cmd, "parse") == 0 && argc == 3) {
      const uint16_t h[5] = {(uint16_t) num(argv[1]), (uint16_t) num(argv[2]), 0x4250u, 0x4331u, 0u};
      bpc1_fields_t f;
      printf("%d\n", bpc1_fields_parse(h, &f) ? 1 : 0);
    } else if (strcmp(cmd, "key") == 0 && argc == 2) {
      printf("0x%08" PRIX32 "\n", sig.keys[num(argv[1])]);
    } else if (strcmp(cmd, "sync") == 0 && argc == 2) {
      bpc1_block_t b;
      block((uint32_t) num(argv[1]), &b);
      for (uint32_t c = 0; c < sig.nch; c++) {
        for (int i = 0; i < 8; i++) {
          printf(i ? " 0x%04X" : "0x%04X", b.sync[c][i]);
        }
        printf("\n");
      }
    } else if (strcmp(cmd, "pcm") == 0 && argc == 3) {
      uint32_t first = (uint32_t) num(argv[1]);
      uint32_t count = (uint32_t) num(argv[2]);
      bpc1_block_t b;
      block(first / sig.units, &b);
      for (uint32_t n = first; n < first + count; n++) {
        if (n / sig.units != b.k) {
          block(n / sig.units, &b);
        }
        for (uint32_t c = 0; c < sig.nch; c++) {
          int32_t w = (int32_t) bpc1_pcm_unit(&sig, &b, c, n % sig.units);
          printf(c ? " %" PRId32 : "%" PRId32, w >> (32 - sig.depth));
        }
        printf("\n");
      }
    } else if (strcmp(cmd, "dsd") == 0 && argc == 3) {
      uint32_t first = (uint32_t) num(argv[1]) / 8u;
      uint32_t count = (uint32_t) num(argv[2]);
      for (uint32_t c = 0; c < sig.nch; c++) {
        bpc1_block_t b;
        block(first / sig.units, &b);
        for (uint32_t u = first; u < first + count; u++) {
          if (u / sig.units != b.k) {
            block(u / sig.units, &b);
          }
          printf("%02X", bpc1_dsd_unit(&sig, &b, c, u % sig.units));
        }
        printf("\n");
      }
    } else {
      printf("error\n");
    }
    fflush(stdout);
  }
  return 0;
}
