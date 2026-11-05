// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// What a display's screen code needs from its panel: a rectangle of pixels
// to write into, and colours given as 0xRRGGBB, which the driver turns into
// the panel's own format. A board with a panel implements it, in the board's
// folder, and lists the source in DISPLAY_SOURCES.
//
// All of it runs in the main loop.

#ifndef PANEL_H
#define PANEL_H

#include <stdbool.h>
#include <stdint.h>

// Bus, reset and the controller's set-up, then the screen cleared to black
// and the backlight on. false: no panel found (the backlight stays off).
bool panel_init(void);

uint16_t panel_width(void);
uint16_t panel_height(void);

// The rectangle that the next panel_fill() and panel_bits() write, left to
// right and top to bottom from its top-left corner.
void panel_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h);

// n pixels of one colour.
void panel_fill(uint32_t rgb, uint32_t n);

// n pixels from a bitmap, the most significant bit of each byte first: fg
// for a 1, bg for a 0.
void panel_bits(const uint8_t *bits, uint32_t n, uint32_t fg, uint32_t bg);

#endif
