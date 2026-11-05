// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The status screen (screen_model.h) on a panel (panel.h), drawn a little
// at a time from the main loop. A board's display.c gives the layout and
// calls these from display_init(), display_update() and display_task().
//
// A layout places the band and the texts it chooses: a small panel the
// labels, the file and the version, a large one the detail rows too.
//
// screen_task() draws only while the USB receive FIFO holds less than
// SCREEN_BACKLOG_US of audio, and for at most about SCREEN_SLICE_US per
// call, interrupts taken meanwhile included: it starts drawing only what
// fits SCREEN_DRAW_US, which leaves the rest for them. The band comes first,
// then each character of the texts that differs from what the panel shows,
// so a changed counter redraws only its glyphs.
//
// Build options: DISPLAY_LOG logs a line per completed redraw,
//   display drawn px=<pixels> busy_us=<drawing time> slices=<calls that drew>
//                 max_slice_us=<longest call> lag_ms=<first change to done>
//                 max_slice_isr_us=<the USB interrupt's time in the longest call>
// and every 10 s while the hourglass blinks outside redraws,
//   display hourglass blinks=<n> max_us=<the longest blink's drawing time>
// and DISPLAY_STRESS redraws everything again after each redraw; with both,
// the line covers the redraws of about a second and adds redraws=<n>.

#ifndef SCREEN_H
#define SCREEN_H

#include <stdbool.h>
#include <stdint.h>

#include "fonts.h"
#include "screen_model.h"
#include "status.h"

#define SCREEN_SLICE_US   200u
#define SCREEN_DRAW_US    160u  // the USB interrupt takes up to about 10 us every 125 us
#define SCREEN_BACKLOG_US 500u

// The texts of screen_t (screen_model.h) a layout can place
typedef enum {
  SCREEN_TEXT_LABELS, // the run's errors
  SCREEN_TEXT_FILE0,  // the file being checked, two lines
  SCREEN_TEXT_FILE1,
  SCREEN_TEXT_FW,     // the firmware version
  SCREEN_TEXT_ROW0,   // the detail rows, SCREEN_TEXT_ROW0 + 0 to SCREEN_ROWS - 1
} screen_text_t;

#define SCREEN_PLACES_MAX (SCREEN_TEXT_ROW0 + SCREEN_ROWS)

typedef struct {
  uint8_t text;              // screen_text_t
  uint16_t y;                // its top; centred across
  const font_t *font;
  uint32_t colour;           // the text's, on black
  bool blink;                // with screen_blink_lit()
} screen_place_t;

typedef struct {
  uint16_t band_h;           // the band: from the top, the full width
  uint16_t band_frame;       // the frame's width
  uint16_t band_margin;      // between the frame and the word, on each side; a
                             // word wider than that leaves breaks at its first
                             // space onto two lines, centred both ways
  const font_t *band_font;
  const screen_place_t *places;  // the texts under the band
  uint8_t n_places;              // at most SCREEN_PLACES_MAX
  // The splash at start-up, centred across: BIT-PERFECT and CHECKER in the
  // band's font from splash_y, the project's address in splash_font at
  // splash_url_y, three squares (red, green, blue) of splash_square pixels
  // at splash_squares_y, and the version where the status screen has it
  uint16_t splash_y;
  const font_t *splash_font;
  uint16_t splash_url_y;
  uint16_t splash_squares_y;
  uint16_t splash_square;
} screen_layout_t;

#define SCREEN_SPLASH_MS 2000u

// After panel_init(), with the panel cleared to black: draws the splash,
// which stays SCREEN_SPLASH_MS before screen_task() draws the status.
void screen_init(const screen_layout_t *layout);

// The splash's square for an LED of the start-up LED test (led_t), lit as
// the LED lights; it stays lit.
void screen_splash_led(uint8_t led);

void screen_update(const status_t *status);
void screen_task(void);

#endif
