// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32F723E-DISCO's display: display_init() brings up the panel and
// the touch controller and times a full-screen fill; the status screen
// (app/screen.h) does the rest, in this board's layout.

#include "display.h"

#include "board.h"
#include "fonts.h"
#include "log.h"
#include "panel.h"
#include "panel_st7789h2.h"
#include "screen.h"
#include "screen_model.h"
#include "touch.h"

// 240x240: the band, 68 lines, a frame of 10 px and one line of the large
// font 8 px from it (NOT CONNECTED fits with 6 px at the sides); the run's
// errors in red in a line of the middle font; the file being checked in two
// lines of the large font, centred between the errors and the version (24
// lines above and below); the firmware version at the bottom
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

void display_init(void) {
  if (!panel_init()) {
    log_line("display st7789h2 id=0x%02x missing", st7789h2_id());
    touch_init();
    return;
  }

  uint32_t pixels = (uint32_t) panel_width() * panel_height();
  uint32_t t0 = board_cycles();
  panel_window(0, 0, panel_width(), panel_height());
  panel_fill(SCREEN_BLACK, pixels);
  uint32_t cycles = board_cycles() - t0;
  uint32_t fill_us = cycles / (board_cpu_hz() / 1000000u);
  log_line("display st7789h2 id=0x%02x fill_us=%u", st7789h2_id(), (unsigned) fill_us);

  screen_init(&layout);
  touch_init();
}

void display_update(const status_t *s) {
  screen_update(s);
}

void display_task(void) {
  screen_task();
}

void display_led_test(led_t led) {
  screen_splash_led((uint8_t) led);
}
