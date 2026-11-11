// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// What the firmware needs from a display. A board or shield with a display
// implements it and lists the sources in its board.cmake or shield.cmake;
// without one, the build links app/display_none.c.
//
// All three run in the main loop, whose passes the device's buffers of
// received audio (about 7 ms at 32/4 on the F723, 169 ms on the U5 boards)
// must not wait for. display_update() is called once at start-up and again
// after each change of the status: it takes the status and does not draw.
// display_task() is called on every pass and draws, a small part at a time
// (about 0.2 ms at most).

#ifndef DISPLAY_H
#define DISPLAY_H

#include "board.h"
#include "status.h"

void display_init(void);
void display_update(const status_t *status);
void display_task(void);

// For the waits of display_init() (a panel's or touch controller's start-up
// times): waits at least ms milliseconds. The device is attached by then,
// and the checker keeps its USB stack running meanwhile (main.c), so that
// the host can enumerate the device; app/display_none.c has a plain wait as
// a default.
void display_wait(uint32_t ms);

// From the start-up LED test, as it lights each LED (also one the board does
// not have): a display can show the same colour. Optional: app/leds.c has a
// default that does nothing.
void display_led_test(led_t led);

#endif
