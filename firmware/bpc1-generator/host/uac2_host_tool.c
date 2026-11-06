// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_uac2_parse.py: runs bpc1-generator/uac2_host.c
// on bytes given in hex on stdin (whitespace between bytes optional, lines
// starting with '#' ignored). Not part of the firmware.
//
//   uac2_host_tool parse
//       a configuration descriptor ->
//       "ok ac=<n> as=<n> clock=<id> clock_set=<0|1> terminal=<id> formats=<list>",
//       then one line per alt setting:
//       "alt <n> slot=<s> res=<r> sync=<none|async|adaptive|sync> out=0x<ep>
//        out_size=<n> fb=0x<ep> fb_size=<n> fb_interval=<n>" (one line),
//       or "refused <reason>"
//   uac2_host_tool range
//       a RANGE reply -> "rates <list>" (empty list: "rates"), or "refused"
//   uac2_host_tool choose <rate> <depth> <dop 0|1>
//       a configuration descriptor -> "alt <n>" or "none"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "uac2_host.h"

#define IN_MAX 65536

static uint8_t in[IN_MAX];

static size_t read_hex(void) {
  size_t n = 0;
  int nibble = -1;
  int c;
  bool comment = false;
  bool line_start = true;
  while ((c = getchar()) != EOF) {
    if (c == '\n') {
      comment = false;
      line_start = true;
      continue;
    }
    if (line_start && c == '#') {
      comment = true;
    }
    line_start = false;
    if (comment || isspace(c)) {
      continue;
    }
    int v = isdigit(c) ? c - '0' : (isxdigit(c) ? tolower(c) - 'a' + 10 : -1);
    if (v < 0) {
      fprintf(stderr, "not hex: %c\n", c);
      exit(2);
    }
    if (nibble < 0) {
      nibble = v;
    } else {
      if (n == IN_MAX) {
        fprintf(stderr, "too long\n");
        exit(2);
      }
      in[n++] = (uint8_t) (nibble << 4 | v);
      nibble = -1;
    }
  }
  if (nibble >= 0) {
    fprintf(stderr, "odd number of hex digits\n");
    exit(2);
  }
  return n;
}

static const char *sync_name(uint8_t sync) {
  static const char *const names[] = {"none", "async", "adaptive", "sync"};
  return sync < 4 ? names[sync] : "?";
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: uac2_host_tool parse|range|choose <rate> <depth> <dop>\n");
    return 2;
  }
  size_t len = read_hex();
  // A copy of exactly len bytes on the heap, so that a read past the end
  // shows under the address sanitizer
  uint8_t *buf = malloc(len > 0 ? len : 1);
  memcpy(buf, in, len);

  if (strcmp(argv[1], "range") == 0) {
    uint32_t rates;
    if (!uac2_host_range(buf, len, &rates)) {
      printf("refused\n");
    } else {
      char text[128];
      uac2_host_rates_text(rates, text, sizeof(text));
      printf("rates%s%s\n", text[0] != '\0' ? " " : "", text);
    }
    free(buf);
    return 0;
  }

  uac2_host_device_t dev;
  const char *reason = uac2_host_parse(buf, len, &dev);
  if (reason != NULL) {
    printf("refused %s\n", reason);
  } else if (strcmp(argv[1], "parse") == 0) {
    char text[128];
    uac2_host_formats_text(&dev, text, sizeof(text));
    printf("ok ac=%u as=%u clock=%u clock_set=%u terminal=%u formats=%s\n", dev.itf_ac, dev.itf_as, dev.clock,
           dev.clock_set ? 1u : 0u, dev.terminal, text);
    for (unsigned i = 0; i < dev.alt_count; i++) {
      const uac2_host_alt_t *a = &dev.alts[i];
      printf("alt %u slot=%u res=%u sync=%s out=0x%02x out_size=%u fb=0x%02x fb_size=%u fb_interval=%u\n", a->alt,
             a->slot, a->res, sync_name(a->sync), a->ep_out, a->out_size, a->ep_fb, a->fb_size, a->fb_interval);
    }
  } else if (strcmp(argv[1], "choose") == 0 && argc == 5) {
    int i = uac2_host_choose(&dev, (uint32_t) strtoul(argv[2], NULL, 10), (uint8_t) atoi(argv[3]),
                             atoi(argv[4]) != 0);
    if (i < 0) {
      printf("none\n");
    } else {
      printf("alt %u\n", dev.alts[i].alt);
    }
  } else {
    fprintf(stderr, "unknown command\n");
    free(buf);
    return 2;
  }
  free(buf);
  return 0;
}
