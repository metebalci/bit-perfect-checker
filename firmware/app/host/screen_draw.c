// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_screen_draw.py: draws the status screen with
// app/screen.c on a 240x240 panel in memory that follows panel.h (a window,
// then pixels left to right and top to bottom; panel_bits() reads n bits, the
// most significant bit of each byte first), in the STM32F723E-DISCO's layout,
// and prints glyphs as the panel shows them. Not part of the firmware.
//
//   glyph <x> <y> <w> <h>   -> h lines of w characters: 1 where the pixel is
//                              not black, else 0
//   pixel <x> <y>           -> its colour, 0xRRGGBB in hex
//
// The status it draws, after the splash's time: mounted, a run with an
// UNKNOWN_SIGNAL result and the last result's file PCM 48 kHz 24-bit. With
// the argument "splash": the splash, with all three squares lit.

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

#define W 240u
#define H 240u

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

// The F723's places (boards/stm32f723e_disco/display.c)
static const screen_place_t places[] = {
  {SCREEN_TEXT_LABELS, 78, &font_12x24, SCREEN_RED, false},
  {SCREEN_TEXT_FILE0, 126, &font_16x32, SCREEN_WHITE, false},
  {SCREEN_TEXT_FILE1, 164, &font_16x32, SCREEN_WHITE, false},
  {SCREEN_TEXT_FW, 220, &font_8x16, SCREEN_WHITE, false},
};

static const screen_layout_t layout = {
  .band_h = 68,
  .band_frame = 10,
  .band_margin = 6,
  .band_font = &font_16x32,
  .places = places,
  .n_places = sizeof(places) / sizeof(places[0]),
  .splash_y = 40,
  .splash_font = &font_12x24,
  .splash_url_y = 124,
  .splash_squares_y = 164,
  .splash_square = 32,
};

int main(int argc, char **argv) {
  bool splash = argc > 1 && strcmp(argv[1], "splash") == 0;
  status.usb = USB_MOUNTED;
  status.high_speed = true;
  status.run_unknown = true;
  status.last_known = true;
  status.last_kind = 1;  // BPC1_KIND_PCM
  status.last_rate = 48000;
  status.last_depth = 24;
  now_ms = 500;
  screen_init(&layout);
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
  }

  char line[128];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    unsigned x, y, w, h;
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
