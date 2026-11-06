// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator's state, as the console shows it (main.c).

#ifndef GENERATOR_H
#define GENERATOR_H

#include <stdbool.h>

// A device is set up (TinyUSB's host has mounted it).
bool generator_usb_mounted(void);

// The VBUS switch: "on", "fault" or "off" (vbus_switch.h).
const char *generator_vbus(void);

#endif
