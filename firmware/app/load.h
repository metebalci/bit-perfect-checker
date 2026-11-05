// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Load figures from the CPU's cycle counter: the time the USB interrupt
// takes, the longest main-loop iteration, and the fullest the receive FIFO
// got. The stream code reads and resets them once per second.

#ifndef LOAD_H
#define LOAD_H

#include <stdint.h>

// From the family's USB interrupt handler: the cycles one call took.
void load_isr(uint32_t cycles);

// From the stream code in the USB interrupt: bytes waiting in the receive
// FIFO after a packet.
void load_fifo(uint32_t bytes);

// From the main loop, once per iteration.
void load_loop(void);

typedef struct {
  uint32_t isr_cycles;  // in the USB interrupt
  uint32_t isr_max;     // the longest call
  uint32_t fifo_max;    // bytes
  uint32_t loop_max;    // cycles of the longest main-loop iteration
} load_t;

// The figures since the last call; starts over.
load_t load_take(void);

// The cycles spent in the USB interrupt since the last load_take(), so far.
uint32_t load_isr_cycles(void);

#endif
