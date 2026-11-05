// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's display, for now a test pattern: display_init()
// brings up the panel and the touch controller, times a full-screen fill and
// draws colour bars, a marker in the top-left corner and text in both fonts.
// display_update() does nothing yet. display_task() polls the touch
// controller and the LTDC's error flags; a DISPLAY_STRESS build also redraws
// the colour bars without a pause, a band at a time, and a DISPLAY_LOG build
// logs the LTDC's FIFO underruns and transfer errors and the touch status
// reads every LOG_MS.

#include "display.h"

#include <stddef.h>

#include "board.h"
#include "fonts.h"
#include "log.h"
#include "panel.h"
#include "panel_rk050hr18.h"
#include "touch.h"

#define WHITE   0xFFFFFFu
#define YELLOW  0xFFFF00u
#define CYAN    0x00FFFFu
#define GREEN   0x00FF00u
#define MAGENTA 0xFF00FFu
#define RED     0xFF0000u
#define BLUE    0x0000FFu
#define BLACK   0x000000u

#define BARS_Y 72u
#define BARS_H 224u
#define BAND_H 16u      // DISPLAY_STRESS redraws this many rows of the bars per pass
#define LOG_MS 10000u   // DISPLAY_LOG

static const uint32_t bars[] = {WHITE, YELLOW, CYAN, GREEN, MAGENTA, RED, BLUE, BLACK};

static bool panel_ok;

static void rect(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint32_t rgb) {
  panel_window(x, y, w, h);
  panel_fill(rgb, (uint32_t) w * h);
}

static void text(uint16_t x, uint16_t y, const font_t *font, const char *s, uint32_t fg, uint32_t bg) {
  for (; *s != '\0'; s++, x = (uint16_t) (x + font->width)) {
    panel_window(x, y, font->width, font->height);
    panel_bits(font_glyph(font, *s), (uint32_t) font->width * font->height, fg, bg);
  }
}

// One row of consecutive characters from first, at most n of them
static void char_row(uint16_t y, const font_t *font, char first, size_t n) {
  char row[51];
  size_t k = 0;
  for (unsigned c = (unsigned char) first; c <= 0x7Eu && k < n && k < sizeof row - 1u; c++) {
    row[k++] = (char) c;
  }
  row[k] = '\0';
  text(0, y, font, row, WHITE, BLACK);
}

static void test_pattern(void) {
  static const char labels[] = "WYCGMRBK";
  uint16_t bar_w = (uint16_t) (panel_width() / 8u);

  // Top-left marker: a red square with a white corner
  rect(0, 0, 64, 64, RED);
  rect(0, 0, 20, 20, WHITE);
  text(80, 0, &font_32x64, "BIT-PERFECT", WHITE, BLACK);

  // Colour bars, each with its initial below it
  for (uint16_t i = 0; i < 8u; i++) {
    uint16_t x = (uint16_t) (i * bar_w);
    char label[2] = {labels[i], '\0'};
    rect(x, BARS_Y, bar_w, BARS_H, bars[i]);
    text((uint16_t) (x + (bar_w - font_16x32.width) / 2u), BARS_Y + BARS_H, &font_16x32, label, WHITE, BLACK);
  }

  // All 95 characters of the small font, 50 to a row, and 25 of the large
  uint16_t y = BARS_Y + BARS_H + 40u;
  char_row(y, &font_16x32, ' ', 50);
  char_row((uint16_t) (y + font_16x32.height), &font_16x32, ' ' + 50, 50);
  char_row((uint16_t) (y + 2u * font_16x32.height + 8u), &font_32x64, '0', 25);
}

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
  panel_fill(BLACK, pixels);
  uint32_t cycles = board_cycles() - t0;
  uint32_t fill_us = cycles / (board_cpu_hz() / 1000000u);
  log_line("display ltdc %ux%u l8 fb=0x%08lx fill_us=%u", (unsigned) panel_width(), (unsigned) panel_height(),
           (unsigned long) panel_rk050hr18_fb(), (unsigned) fill_us);

  test_pattern();
#ifdef DISPLAY_LOG
  // One glyph of each font, drawn again over itself
  uint32_t mhz = board_cpu_hz() / 1000000u;
  t0 = board_cycles();
  text(80, 0, &font_32x64, "B", WHITE, BLACK);
  uint32_t large = board_cycles() - t0;
  t0 = board_cycles();
  text((uint16_t) ((panel_width() / 8u - font_16x32.width) / 2u), BARS_Y + BARS_H, &font_16x32, "W", WHITE, BLACK);
  uint32_t small = board_cycles() - t0;
  log_line("display glyph_us 16x32=%u 32x64=%u", (unsigned) (small / mhz), (unsigned) (large / mhz));
#endif
  touch_init();
}

void display_update(const status_t *s) {
  (void) s;
}

#ifdef DISPLAY_STRESS
// One band of the colour bars, each pass of the whole bars area with the
// colours moved on by one bar
static void stress_slice(void) {
  static uint16_t band;
  static uint16_t pass;
  uint16_t bar_w = (uint16_t) (panel_width() / 8u);
  for (uint16_t i = 0; i < 8u; i++) {
    rect((uint16_t) (i * bar_w), (uint16_t) (BARS_Y + band * BAND_H), bar_w, BAND_H, bars[(i + pass) & 7u]);
  }
  if (++band >= BARS_H / BAND_H) {
    band = 0;
    pass++;
  }
}
#endif

void display_task(void) {
  touch_task();
  if (!panel_ok) {
    return;
  }
  uint32_t underruns;
  uint32_t transfer_errors;
  panel_rk050hr18_errors(&underruns, &transfer_errors);
#ifdef DISPLAY_STRESS
  stress_slice();
#endif
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
