// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32F723E-DISCO's status screen layout (app/screen.h), shared by its
// display.c and the host tests (tests/test_screen_draw.py).

#ifndef SCREEN_LAYOUT_H
#define SCREEN_LAYOUT_H

#include "fonts.h"
#include "screen.h"
#include "screen_model.h"

// Once, before screen_init(&layout): nothing to make on this board, whose
// fonts are all in flash
static inline void screen_layout_init(void) {
}

// 240x240: the band, 68 lines, a frame of 10 px and one line of the large
// font 8 px from it (NOT CONNECTED fits with 6 px at the sides); the run's
// errors in red in a line of the middle font; the file being checked in two
// lines of the large font, centred between the errors and the version (24
// lines above and below); the firmware version at the bottom
static const screen_place_t places[] = {
  {SCREEN_TEXT_LABELS, 78, &font_12x24, SCREEN_RED, false, 0, 0},
  {SCREEN_TEXT_FILE0, 126, &font_16x32, SCREEN_WHITE, false, 0, 0},
  {SCREEN_TEXT_FILE1, 164, &font_16x32, SCREEN_WHITE, false, 0, 0},
  {SCREEN_TEXT_FW, 220, &font_8x16, SCREEN_WHITE, false, 0, 0},
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

#endif
