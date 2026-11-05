// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's status screen layout (app/screen.h), shared by its
// display.c and the host tests (tests/test_screen_draw.py).
//
// The left 480x480 of the 800x480 panel shows the STM32F723E-DISCO's 240x240
// screen at twice the size: every position and size doubled, the fonts
// twice as large (8x16 -> Spleen's 16x32, 16x32 -> Spleen's 32x64), the
// hourglass doubled. Spleen has no 24x48 for the F723's 12x24, so the labels
// and the splash's address use the 12x24 doubled pixel for pixel, made in
// RAM at start-up by screen_layout_init(). The right 320x480 (x 480 to 799)
// stays black for now: a place with x = SCREEN_RIGHT_X and width =
// SCREEN_RIGHT_W puts a text there.

#ifndef SCREEN_LAYOUT_H
#define SCREEN_LAYOUT_H

#include "fonts.h"
#include "screen.h"
#include "screen_model.h"

#define SCREEN_LEFT_W  480u
#define SCREEN_RIGHT_X 480u
#define SCREEN_RIGHT_W 320u

// Spleen's 12x24 at twice the size
static uint8_t font_24x48_bits[SCREEN_FONT_DOUBLE_BYTES(12u, 24u, 95u)];
static font_t font_24x48;

// Once, before screen_init(&layout)
static inline void screen_layout_init(void) {
  screen_font_double(&font_12x24, &font_24x48, font_24x48_bits);
}

// The band, 136 lines, a frame of 20 px and one line of the large font 12 px
// from it; the run's errors in red; the file being checked in two lines of
// the large font; the firmware version at the bottom
static const screen_place_t places[] = {
  {SCREEN_TEXT_LABELS, 156, &font_24x48, SCREEN_RED, false, 0, 0},
  {SCREEN_TEXT_FILE0, 252, &font_32x64, SCREEN_WHITE, false, 0, 0},
  {SCREEN_TEXT_FILE1, 328, &font_32x64, SCREEN_WHITE, false, 0, 0},
  {SCREEN_TEXT_FW, 440, &font_16x32, SCREEN_WHITE, false, 0, 0},
};

static const screen_layout_t layout = {
  .x = 0,
  .width = SCREEN_LEFT_W,
  .band_h = 136,
  .band_frame = 20,
  .band_margin = 12,
  .band_font = &font_32x64,
  .hourglass_scale = 2,
  .places = places,
  .n_places = sizeof(places) / sizeof(places[0]),
  .splash_y = 80,
  .splash_font = &font_24x48,
  .splash_url_y = 248,
  .splash_squares_y = 328,
  .splash_square = 64,
};

#endif
