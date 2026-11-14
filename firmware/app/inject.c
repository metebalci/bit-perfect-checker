// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Late re-arms on purpose, for test builds only, on u2ps's injection hook
// (u2ps_dwc2_inject(), which BPC_TEST_INJECT builds with U2PS_TEST_INJECT).
// See inject.h.

#include "inject.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "board.h"
#include "log.h"
#include "u2ps_dwc2_dcd.h"

#define INJECT_US_MAX 1000u

static bool parse_u32(const char **s, uint32_t *v) {
  const char *p = *s;
  while (*p == ' ') {
    p++;
  }
  if (*p < '0' || *p > '9') {
    return false;
  }
  uint32_t x = 0;
  while (*p >= '0' && *p <= '9' && x <= 100000000u) {
    x = x * 10u + (uint32_t) (*p++ - '0');
  }
  *s = p;
  *v = x;
  return true;
}

static uint32_t ns(uint32_t cycles) {
  return (uint32_t) (((uint64_t) cycles * 1000u) / (board_cpu_hz() / 1000000u));
}

// "inject passes [clear]": u2ps's handler time by kind of pass, one line per
// kind: passes, the longest, the mean and the sum, in DWT cycles
static void passes(bool clear) {
  static const char *const kinds[U2PS_DWC2_PASS_KINDS] = U2PS_DWC2_PASS_NAMES;
  u2ps_dwc2_passes_t p;
  u2ps_dwc2_passes_get(&p, clear);
  for (int k = 0; k < U2PS_DWC2_PASS_KINDS; k++) {
    // newlib-nano's printf has no 64-bit conversions: the sum in kilocycles
    log_line("passes kind=%s n=%lu max=%lu mean=%lu sum_k=%lu", kinds[k], (unsigned long) p.n[k],
             (unsigned long) p.max[k], (unsigned long) (p.n[k] ? p.sum[k] / p.n[k] : 0u),
             (unsigned long) (p.sum[k] / 1000u));
  }
}

void inject_command(const char *arg) {
  if (arg != NULL && strncmp(arg, "passes", 6) == 0) {
    passes(strcmp(arg, "passes clear") == 0);
    return;
  }
  if (arg != NULL && strcmp(arg, "off") == 0) {
    u2ps_dwc2_inject(0, 0);
  } else if (arg != NULL) {
    const char *p = arg;
    uint32_t n, us;
    if (!parse_u32(&p, &n) || !parse_u32(&p, &us) || *p != '\0' || n == 0u || us > INJECT_US_MAX) {
      log_line("error inject=%s", arg);
      return;
    }
    u2ps_dwc2_inject(n, us);
  }
  u2ps_dwc2_inject_t st;
  u2ps_dwc2_inject_get(&st);
  log_line("inject every=%lu us=%lu injected=%lu skipped=%lu timeouts=%lu d_min_ns=%lu d_max_ns=%lu",
           (unsigned long) st.every, (unsigned long) st.us, (unsigned long) st.injected,
           (unsigned long) st.skipped, (unsigned long) st.timeouts, (unsigned long) ns(st.d_min),
           (unsigned long) ns(st.d_max));
}
