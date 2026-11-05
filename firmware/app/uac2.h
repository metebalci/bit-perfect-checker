// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The USB audio function: sample rate, alt settings, feedback, and which
// formats the device offers.

#ifndef UAC2_H
#define UAC2_H

#include <stdbool.h>

// Before tusb_init(): offer the build's default formats (UAC2_FORMATS).
void uac2_init(void);

// Offer other formats, as "<bits>/<bytes>,..." or "default"; NULL just logs
// the current ones. A change disconnects the device and reconnects it
// shortly after, so the host enumerates the new formats. False if the text
// is not a valid list.
bool uac2_formats(const char *text);

// VBUS has gone (main.c): ends the configuration at once, with the lines
// of a stream it stops; the caller logs the disconnect.
void uac2_unplug(void);

#endif
