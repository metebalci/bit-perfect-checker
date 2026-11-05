// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The LEDs, from the status:
//   blue, fast blink   not connected: no host has set the device up, or
//                      the cable is out (no VBUS)
//   blue, slow blink   waiting for a stream
//   blue, steady       streaming
// Green and red show the run (since the user button, `mark`, a pulled USB
// cable or reset), the
// first that holds:
//   red, steady        a NOT_BIT_PERFECT file (from its first difference on)
//   red, slow blink    an UNKNOWN_SIGNAL result
//   red, fast blink    an OVERRUN result
//   green              the results are all BIT_PERFECT
//   neither            nothing judged yet
//   all three          fault (set by the board's fault handler)

#ifndef LEDS_H
#define LEDS_H

// At start-up: red, green, then blue, each for 0.3 s, logged as "led <name>";
// a display shows each colour too (display_led_test()).
void leds_test(void);

// From the main loop.
void leds_task(void);

#endif
