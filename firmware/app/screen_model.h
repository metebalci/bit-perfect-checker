// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// What a status screen shows, from the status: a band, labels for the
// run's errors, the file being checked and the firmware version,
// and SCREEN_ROWS rows of detail for a screen large enough for them. Pure C,
// no hardware: screen.c draws it, and the host tests check it against
// app/leds.c.
//
// The band is one line in a thick frame, black inside, the word in white.
// The word says what is going on: NOT CONNECTED (no host), WAITING (no
// stream), CHECKING (a stream open); WAITING and CHECKING have an hourglass
// right of the word, blinking, CHECKING's only while packets arrive. The
// frame's colour is the run's state, as the LEDs show it: blue with no
// result yet, green while every result is BIT_PERFECT, red as soon as
// anything failed (a file's first difference, NOT_BIT_PERFECT,
// UNKNOWN_SIGNAL, OVERRUN). Labels name the run's errors in red:
// UNKNOWN and OVERRUN. Verdicts are written without the log's
// underscores and BIT-PERFECT as in the project's name.

#ifndef SCREEN_MODEL_H
#define SCREEN_MODEL_H

#include <stdbool.h>
#include <stdint.h>

#include "status.h"

#define SCREEN_ROWS 9u
#define SCREEN_COLS 30u
#define SCREEN_BAND_COLS 15u
#define SCREEN_LABEL_COLS 20u

// Colours as 0xRRGGBB
#define SCREEN_BLACK 0x000000u
#define SCREEN_WHITE 0xFFFFFFu
#define SCREEN_RED   0xFF0000u
#define SCREEN_GREEN 0x00FF00u
#define SCREEN_BLUE  0x0000FFu

// The band's states
typedef enum {
  SCREEN_NOT_CONNECTED,
  SCREEN_WAITING,
  SCREEN_CHECKING,
} screen_state_t;

// How the hourglass shows
typedef enum {
  SCREEN_HOURGLASS_NONE,
  SCREEN_HOURGLASS_BLINK,
  SCREEN_HOURGLASS_STEADY,  // CHECKING while no packet arrives
} screen_hourglass_t;

typedef struct {
  uint8_t state;   // screen_state_t
  uint32_t colour; // the frame's
  bool hourglass;  // WAITING and CHECKING: an hourglass right of the word
  char word[SCREEN_BAND_COLS + 1];
} screen_band_t;

// The texts a layout can place, each padded with spaces to its width
typedef struct {
  screen_band_t band;
  // The run's errors, each only if it occurred, in one line of
  // SCREEN_LABEL_COLS, a column in from each side: UNKNOWN (UNKNOWN_SIGNAL)
  // left, OVERRUN right; a screen shows them in red
  char labels[SCREEN_LABEL_COLS + 1];
  // The file being checked, as its header gives it, in two lines centred in
  // SCREEN_BAND_COLS (PCM / 24/96, DoP / DSD128, PCM SWEEP / 16/44.1): while
  // a stream is open the file the checker locked onto, else the last
  // result's file; blank without one
  char file[2][SCREEN_BAND_COLS + 1];
  // The firmware version, centred, without a label
  char fw[SCREEN_COLS + 1];
  // A large screen's detail rows
  char rows[SCREEN_ROWS][SCREEN_COLS + 1];
} screen_t;

// fw: the firmware version, as the boot line gives it
void screen_model(const status_t *s, const char *fw, screen_t *out);

// How the band's hourglass shows; moving: packets arrived just now
uint8_t screen_hourglass(const screen_band_t *band, bool moving);

// The screen's blink at board_millis() ms: 500 ms on, 500 ms off, on the
// LEDs' time base and phase (leds.c), so that the hourglass in WAITING lights
// with the blue LED (and a blinking text, screen.h, with a slow red one)
bool screen_blink_lit(uint32_t ms);

// Whether the hourglass is lit at ms
bool screen_hourglass_lit(uint8_t hourglass, uint32_t ms);

#endif
