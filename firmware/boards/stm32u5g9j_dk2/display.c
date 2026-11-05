// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's display: display_init() brings up the panel and the
// touch controller and times a full-screen fill; the status screen
// (app/screen.h) does the rest, in this board's layout (screen_layout.h).
// display_task() also polls the touch controller and the LTDC's error flags;
// a DISPLAY_LOG build logs the LTDC's FIFO underruns and transfer errors and
// the touch status reads every LOG_MS.

#include "display.h"

#include "board.h"
#include "fonts.h"
#include "log.h"
#include "panel.h"
#include "panel_rk050hr18.h"
#include "screen.h"
#include "screen_layout.h"
#include "screen_model.h"
#include "touch.h"

#define LOG_MS 10000u  // DISPLAY_LOG

static bool panel_ok;

void display_init(void) {
  panel_ok = panel_init();
  if (!panel_ok) {
    log_line("display ltdc missing");
    touch_init();
    return;
  }
  if (panel_rk050hr18_sram6_was_off()) {
    log_line("display sram6 clock was off");
  }

  uint32_t pixels = (uint32_t) panel_width() * panel_height();
  uint32_t t0 = board_cycles();
  panel_window(0, 0, panel_width(), panel_height());
  panel_fill(SCREEN_BLACK, pixels);
  uint32_t cycles = board_cycles() - t0;
  uint32_t fill_us = cycles / (board_cpu_hz() / 1000000u);
  log_line("display ltdc %ux%u l8 fb=0x%08lx fill_us=%u", (unsigned) panel_width(), (unsigned) panel_height(),
           (unsigned long) panel_rk050hr18_fb(), (unsigned) fill_us);

  screen_layout_init();
  screen_init(&layout);
  touch_init();
}

void display_update(const status_t *s) {
  screen_update(s);
}

void display_task(void) {
  touch_task();
  if (!panel_ok) {
    return;
  }
  screen_task();
  uint32_t underruns;
  uint32_t transfer_errors;
  panel_rk050hr18_errors(&underruns, &transfer_errors);
#ifdef DISPLAY_LOG
  static uint32_t logged_ms;
  static uint32_t logged_reads;
  uint32_t now = board_millis();
  if (now - logged_ms >= LOG_MS) {
    uint32_t reads = touch_reads();
    logged_ms = now;
    log_line("display ltdc fuif=%lu terrif=%lu touch_reads=%lu", (unsigned long) underruns,
             (unsigned long) transfer_errors, (unsigned long) (reads - logged_reads));
    logged_reads = reads;
  }
#else
  (void) underruns;
  (void) transfer_errors;
#endif
}

void display_led_test(led_t led) {
  screen_splash_led((uint8_t) led);
}
