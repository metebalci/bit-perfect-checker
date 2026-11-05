// The STM32F723E-DISCO's display, for now a test pattern: display_init()
// brings up the panel and the touch controller, times a full-screen fill and
// draws colour bars, a marker in the top-left corner and every character of
// both fonts. display_update() and display_task() do nothing yet.

#include "display.h"

#include <stddef.h>

#include "board.h"
#include "fonts.h"
#include "log.h"
#include "panel.h"
#include "panel_st7789h2.h"
#include "touch.h"

#define WHITE   0xFFFFFFu
#define YELLOW  0xFFFF00u
#define CYAN    0x00FFFFu
#define GREEN   0x00FF00u
#define MAGENTA 0xFF00FFu
#define RED     0xFF0000u
#define BLUE    0x0000FFu
#define BLACK   0x000000u

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

static void test_pattern(void) {
  static const uint32_t bars[] = {WHITE, YELLOW, CYAN, GREEN, MAGENTA, RED, BLUE, BLACK};
  static const char labels[] = "WYCGMRBK";
  uint16_t bar_w = (uint16_t) (panel_width() / 8u);

  // Top-left marker: a red square with a white corner
  rect(0, 0, 32, 32, RED);
  rect(0, 0, 10, 10, WHITE);
  text(40, 0, &font_16x32, "BIT-PERFECT", WHITE, BLACK);

  // Colour bars, each with its initial below it
  for (uint16_t i = 0; i < 8u; i++) {
    uint16_t x = (uint16_t) (i * bar_w);
    char label[2] = {labels[i], '\0'};
    rect(x, 40, bar_w, 120, bars[i]);
    text((uint16_t) (x + (bar_w - font_8x16.width) / 2u), 160, &font_8x16, label, WHITE, BLACK);
  }

  // All 95 characters of the small font, 30 to a row
  char row[31];
  for (uint16_t r = 0; r < 4u; r++) {
    size_t n = 0;
    for (uint16_t c = (uint16_t) (0x20u + r * 30u); c <= 0x7Eu && n < 30u; c++) {
      row[n++] = (char) c;
    }
    row[n] = '\0';
    text(0, (uint16_t) (176u + r * font_8x16.height), &font_8x16, row, WHITE, BLACK);
  }
}

void display_init(void) {
  if (!panel_init()) {
    log_line("display st7789h2 id=0x%02x missing", st7789h2_id());
    touch_init();
    return;
  }

  uint32_t pixels = (uint32_t) panel_width() * panel_height();
  uint32_t t0 = board_cycles();
  panel_window(0, 0, panel_width(), panel_height());
  panel_fill(BLACK, pixels);
  uint32_t cycles = board_cycles() - t0;
  uint32_t fill_us = cycles / (board_cpu_hz() / 1000000u);
  log_line("display st7789h2 id=0x%02x fill_us=%u", st7789h2_id(), (unsigned) fill_us);

  test_pattern();
  touch_init();
}

void display_update(const status_t *s) {
  (void) s;
}

void display_task(void) {
}
