// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Late re-arms on purpose, for test builds only. See inject.h.

#include "inject.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "board.h"
#include "log.h"
#include "tusb.h"
#include "usb_descriptors.h"

#define INJECT_US_MAX 1000u

bool __real_dcd_edpt_xfer(uint8_t rhport, uint8_t ep_addr, uint8_t *buffer, uint16_t total_bytes, bool is_isr);
bool __wrap_dcd_edpt_xfer(uint8_t rhport, uint8_t ep_addr, uint8_t *buffer, uint16_t total_bytes, bool is_isr);

static volatile uint32_t every;   // 0: off
static volatile uint32_t wait_cycles;
static uint32_t wait_us;

// Written by the USB interrupt
static uint32_t rearms;  // since the last injection
static uint32_t injected, skipped, timeouts;
static uint32_t d_min, d_max;  // cycles

bool __wrap_dcd_edpt_xfer(uint8_t rhport, uint8_t ep_addr, uint8_t *buffer, uint16_t total_bytes, bool is_isr) {
  uint32_t n = every;
  if (n != 0u && is_isr && ep_addr == EP_AUDIO_OUT && ++rearms >= n) {
    uint32_t fn;
    bool odd;
    board_usb_entry(&fn, &odd);
    if (((fn ^ (odd ? 1u : 0u)) & 1u) == 0u && board_usb_microframe() == fn) {
      rearms = 0;
      uint32_t t0 = board_cycles();
      uint32_t limit = board_cpu_hz() / 4000u;  // two microframes
      while (board_usb_microframe() == fn && board_cycles() - t0 < limit) {
      }
      if (board_usb_microframe() == fn) {
        timeouts++;
      }
      uint32_t t1 = board_cycles();
      uint32_t w = wait_cycles;
      while (board_cycles() - t1 < w) {
      }
      bool ok = __real_dcd_edpt_xfer(rhport, ep_addr, buffer, total_bytes, is_isr);
      uint32_t d = board_cycles() - t1;
      if (injected == 0u || d < d_min) {
        d_min = d;
      }
      if (d > d_max) {
        d_max = d;
      }
      injected++;
      return ok;
    }
    skipped++;
  }
  return __real_dcd_edpt_xfer(rhport, ep_addr, buffer, total_bytes, is_isr);
}

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

void inject_command(const char *arg) {
  if (arg != NULL && strcmp(arg, "off") == 0) {
    every = 0;
  } else if (arg != NULL) {
    const char *p = arg;
    uint32_t n, us;
    if (!parse_u32(&p, &n) || !parse_u32(&p, &us) || *p != '\0' || n == 0u || us > INJECT_US_MAX) {
      log_line("error inject=%s", arg);
      return;
    }
    every = 0;
    board_irq_disable();
    rearms = 0;
    injected = 0;
    skipped = 0;
    timeouts = 0;
    d_min = 0;
    d_max = 0;
    board_irq_enable();
    wait_us = us;
    wait_cycles = (uint32_t) (((uint64_t) us * board_cpu_hz()) / 1000000u);
    every = n;
  }
  board_irq_disable();
  uint32_t i = injected, s = skipped, t = timeouts, lo = d_min, hi = d_max;
  board_irq_enable();
  log_line("inject every=%lu us=%lu injected=%lu skipped=%lu timeouts=%lu d_min_ns=%lu d_max_ns=%lu",
           (unsigned long) every, (unsigned long) wait_us, (unsigned long) i, (unsigned long) s, (unsigned long) t,
           (unsigned long) ns(lo), (unsigned long) ns(hi));
}
