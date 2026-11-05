// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_screen_slice.py: runs app/screen.c with a board's
// layout on a panel whose drawing time is a model, and prints how long each
// call of screen_task() drew, so that the test can hold the calls to
// SCREEN_DRAW_US. Not part of the firmware. Built with -DDRAW_W=<w>
// -DDRAW_H=<h> -DDRAW_LAYOUT=<header>, a board's panel size and its layout
// header (one that defines `layout` and screen_layout_init(), as
// boards/stm32u5g9j_dk2/screen_layout.h).
//
// The model: panel_window() takes WINDOW_CYCLES, panel_fill() CALL_CYCLES
// and FILL_CYCLES a pixel, panel_bits() CALL_CYCLES and BITS_CYCLES a
// pixel; nothing else takes time, interrupts included. The CPU runs at
// CPU_HZ.
//
// It draws the status after the splash: the clear of the whole panel, then
// the band and the texts, with the status of app/host/screen_draw.c, and
// prints
//   budget <SCREEN_DRAW_US in cycles>
//   slice <cycles> <pixels>     one line per call of screen_task() that drew

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "fonts.h"
#include "load.h"
#include "log.h"
#include "panel.h"
#include "screen.h"
#include "status.h"
#include "stream.h"

#define CPU_HZ        160000000u
#define WINDOW_CYCLES 20u
#define CALL_CYCLES   10u
#define FILL_CYCLES   1u
#define BITS_CYCLES   3u

status_t status;

static uint32_t now_ms;
static uint32_t cycles;
static uint32_t pixels;

uint32_t board_millis(void) {
  return now_ms;
}

uint32_t board_cycles(void) {
  return cycles;
}

uint32_t board_cpu_hz(void) {
  return CPU_HZ;
}

void log_line(const char *fmt, ...) {
  (void) fmt;
}

uint32_t load_isr_cycles(void) {
  return 0;
}

uint32_t stream_backlog_us(void) {
  return 0;
}

uint32_t stream_packets(void) {
  return 0;
}

uint16_t panel_width(void) {
  return DRAW_W;
}

uint16_t panel_height(void) {
  return DRAW_H;
}

void panel_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  (void) x;
  (void) y;
  (void) w;
  (void) h;
  cycles += WINDOW_CYCLES;
}

void panel_fill(uint32_t rgb, uint32_t n) {
  (void) rgb;
  cycles += CALL_CYCLES + FILL_CYCLES * n;
  pixels += n;
}

void panel_bits(const uint8_t *bits, uint32_t n, uint32_t fg, uint32_t bg) {
  (void) bits;
  (void) fg;
  (void) bg;
  cycles += CALL_CYCLES + BITS_CYCLES * n;
  pixels += n;
}

#include DRAW_LAYOUT

int main(void) {
  status.usb = USB_MOUNTED;
  status.high_speed = true;
  status.run_unknown = true;
  status.last_known = true;
  status.last_in_run = true;
  status.last_kind = 1;  // BPC1_KIND_PCM
  status.last_rate = 48000;
  status.last_depth = 24;
  now_ms = 500;
  screen_layout_init();
  screen_init(&layout);
  screen_update(&status);
  now_ms += SCREEN_SPLASH_MS;  // the splash's time is up

  printf("budget %lu\n", (unsigned long) (SCREEN_DRAW_US * (CPU_HZ / 1000000u)));
  for (int i = 0; i < 10000; i++) {
    uint32_t c0 = cycles;
    uint32_t p0 = pixels;
    screen_task();
    if (pixels != p0) {
      printf("slice %lu %lu\n", (unsigned long) (cycles - c0), (unsigned long) (pixels - p0));
    }
  }
  return 0;
}
