// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The USB audio function on u2ps (uac2-playback-stack): sample rate, alt
// settings, feedback, which formats the device offers, and the stream's
// buffers (u2ps_glue.c).

#ifndef UAC2_H
#define UAC2_H

#include <stdbool.h>
#include <stdint.h>

// First: offer the build's default formats (UAC2_FORMATS).
void uac2_init(void);

// After uac2_init(): starts u2ps and allows the attach; the D+ pull-up comes
// on once uac2_vbus() reports VBUS. A failed start is logged and lights
// every LED.
void uac2_start(void);

// At every pass of the main loop: u2ps's tick and the events of its
// callbacks.
void uac2_task(void);

// No packet, zero-length ones included, for longer than u2ps's long gap
// (U2PS_GAP_PAUSE / 8 ms), as the last tick (uac2_task()) saw it: u2ps has
// reported every hole of the open stream up to then.
bool uac2_quiet(void);

// The D+ pull-up allowed or not (vbus.h).
void uac2_pullup(bool on);

// At every VBUS poll of the main loop: the level.
void uac2_vbus(bool present);

// From stream.c, with interrupts off: the n oldest packets the stream module
// holds are read, and their buffers go back to u2ps, if the stream is still
// open; a stream u2ps stopped has its buffers back already.
void uac2_release(uint32_t n);

// Offer other formats, as "<bits>/<bytes>,..." or "default"; NULL just logs
// the current ones. A change disconnects the device and reconnects it
// shortly after, so the host enumerates the new formats. False if the text
// is not a valid list.
bool uac2_formats(const char *text);

// VBUS has gone (main.c): ends the configuration at once, with the lines
// of a stream it stops; the caller logs the disconnect.
void uac2_unplug(void);

#endif
