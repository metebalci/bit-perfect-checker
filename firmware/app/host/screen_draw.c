// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_screen_draw.py: draws the status screen with
// app/screen.c on a panel in memory that follows panel.h (a window, then
// pixels left to right and top to bottom; panel_bits() reads n bits, the
// most significant bit of each byte first), and prints glyphs as the panel
// shows them. Not part of the firmware. Built with -DDRAW_W=<w> -DDRAW_H=<h>
// -DDRAW_LAYOUT=<header>, a board's panel size and its layout header, the
// one its display.c uses (boards/<board>/screen_layout.h, which defines
// `layout` and screen_layout_init()); -DDRAW_AREA_X=<x> moves that layout's
// area to x, for tests of the area's offset.
//
//   glyph <x> <y> <w> <h>   -> h lines of w characters: 1 where the pixel is
//                              not black, else 0
//   pixel <x> <y>           -> its colour, 0xRRGGBB in hex
//   hash                    -> a hash of every pixel's colour (FNV-1a), in hex
//
// The status it draws, after the splash's time: mounted, a run with an
// UNKNOWN_SIGNAL result and the last result's file PCM 48 kHz 24-bit. With
// the argument "splash": the splash, with all three squares lit; with
// "not-connected": the same with no host (the band's NOT CONNECTED, which
// has no hourglass); with "old-run": the same, but the last result belongs
// to the run before (a run started since it); with "full-speed": the same
// set up at Full-Speed (the tall band, over the texts above its bottom); with
// "full-speed-back": Full-Speed drawn, then High-Speed (as without an
// argument, once drawn); with "full-speed-between": High-Speed drawn, then
// Full-Speed, then High-Speed again.

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "fonts.h"
#include "load.h"
#include "log.h"
#include "panel.h"
#include "screen.h"
#include "status.h"
#include "stream.h"

#if !defined(DRAW_W) || !defined(DRAW_H) || !defined(DRAW_LAYOUT)
#error "build with -DDRAW_W=<w> -DDRAW_H=<h> -DDRAW_LAYOUT=<header>"
#endif
#define W DRAW_W
#define H DRAW_H

status_t status;

static uint32_t fb[H][W];
static uint16_t win_x, win_y, win_w, win_h;
static uint32_t win_i;
static uint32_t now_ms;

uint32_t board_millis(void) {
  return now_ms;
}

uint32_t board_cycles(void) {
  static uint32_t c;
  return c += 100u;
}

uint32_t board_cpu_hz(void) {
  return 216000000u;
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
  return W;
}

uint16_t panel_height(void) {
  return H;
}

void panel_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  win_x = x;
  win_y = y;
  win_w = w;
  win_h = h;
  win_i = 0;
}

static void pixel(uint32_t rgb) {
  uint32_t x = win_x + win_i % win_w;
  uint32_t y = win_y + win_i / win_w;
  if (win_i < (uint32_t) win_w * win_h && x < W && y < H) {
    fb[y][x] = rgb;
  }
  win_i++;
}

void panel_fill(uint32_t rgb, uint32_t n) {
  while (n-- > 0) {
    pixel(rgb);
  }
}

void panel_bits(const uint8_t *bits, uint32_t n, uint32_t fg, uint32_t bg) {
  for (uint32_t i = 0; i < n; i++) {
    pixel((bits[i >> 3] & (0x80u >> (i & 7u))) != 0 ? fg : bg);
  }
}

#include DRAW_LAYOUT

int main(int argc, char **argv) {
  bool splash = argc > 1 && strcmp(argv[1], "splash") == 0;
  bool not_connected = argc > 1 && strcmp(argv[1], "not-connected") == 0;
  bool old_run = argc > 1 && strcmp(argv[1], "old-run") == 0;
  bool full_speed = argc > 1 && strncmp(argv[1], "full-speed", 10) == 0;
  bool back = argc > 1 && strcmp(argv[1], "full-speed-back") == 0;
  bool between = argc > 1 && strcmp(argv[1], "full-speed-between") == 0;
  if (between) {
    full_speed = false;
  }
  status.usb = not_connected ? USB_NOT_MOUNTED : USB_MOUNTED;
  status.high_speed = !full_speed;
  status.run_unknown = true;
  status.last_known = true;
  status.last_in_run = !old_run;
  status.last_kind = 1;  // BPC1_KIND_PCM
  status.last_rate = 48000;
  status.last_depth = 24;
  now_ms = 500;
  screen_layout_init();
#ifdef DRAW_AREA_X
  static screen_layout_t moved;
  moved = layout;
  moved.x = DRAW_AREA_X;
  screen_init(&moved);
#else
  screen_init(&layout);
#endif
  if (splash) {
    screen_splash_led(LED_RED);
    screen_splash_led(LED_GREEN);
    screen_splash_led(LED_BLUE);
  } else {
    screen_update(&status);
    now_ms += SCREEN_SPLASH_MS;  // the splash's time is up
    for (int i = 0; i < 1000; i++) {
      screen_task();
    }
    // the speeds that follow
    for (int step = 0; step < (between ? 2 : back ? 1 : 0); step++) {
      status.high_speed = between && step == 0 ? false : true;
      screen_update(&status);
      for (int i = 0; i < 1000; i++) {
        screen_task();
      }
    }
  }

  char line[128];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    unsigned x, y, w, h;
    if (strncmp(line, "hash", 4) == 0) {
      uint32_t hash = 2166136261u;
      for (uint32_t i = 0; i < (uint32_t) W * H; i++) {
        hash = (hash ^ fb[i / W][i % W]) * 16777619u;
      }
      printf("%08x\n", (unsigned) hash);
      continue;
    }
    if (sscanf(line, "pixel %u %u", &x, &y) == 2) {
      printf("%06x\n", x < W && y < H ? (unsigned) fb[y][x] : 0u);
      continue;
    }
    if (sscanf(line, "glyph %u %u %u %u", &x, &y, &w, &h) != 4) {
      fprintf(stderr, "bad command %s", line);
      return 1;
    }
    for (unsigned r = 0; r < h; r++) {
      for (unsigned c = 0; c < w; c++) {
        putchar(y + r < H && x + c < W && fb[y + r][x + c] != SCREEN_BLACK ? '1' : '0');
      }
      putchar('\n');
    }
  }
  return 0;
}
