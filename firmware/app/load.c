// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Load figures; see load.h.

#include "load.h"

#include "board.h"

static volatile uint32_t isr_cycles;
static volatile uint32_t isr_max;
static volatile uint32_t fifo_max;
static uint32_t loop_last;
static uint32_t loop_max;

void load_isr(uint32_t cycles) {
  isr_cycles += cycles;
  if (cycles > isr_max) {
    isr_max = cycles;
  }
}

void load_fifo(uint32_t bytes) {
  if (bytes > fifo_max) {
    fifo_max = bytes;
  }
}

void load_loop(void) {
  uint32_t now = board_cycles();
  uint32_t dt = now - loop_last;
  loop_last = now;
  if (dt > loop_max) {
    loop_max = dt;
  }
}

load_t load_take(void) {
  load_t l;
  board_irq_disable();
  l.isr_cycles = isr_cycles;
  l.isr_max = isr_max;
  l.fifo_max = fifo_max;
  isr_cycles = 0;
  isr_max = 0;
  fifo_max = 0;
  board_irq_enable();
  l.loop_max = loop_max;
  loop_max = 0;
  return l;
}
