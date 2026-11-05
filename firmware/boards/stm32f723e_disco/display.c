// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32F723E-DISCO's display: display_init() brings up the panel and
// the touch controller and times a full-screen fill; the status screen
// (app/screen.h) does the rest, in this board's layout (screen_layout.h).

#include "display.h"

#include "board.h"
#include "fonts.h"
#include "log.h"
#include "panel.h"
#include "panel_st7789h2.h"
#include "screen.h"
#include "screen_layout.h"
#include "screen_model.h"
#include "touch.h"

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

  screen_layout_init();
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
