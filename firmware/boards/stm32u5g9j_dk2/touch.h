// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's touch panel as a second user button: a finger
// anywhere on the screen counts as a press of B2.

#ifndef TOUCH_H
#define TOUCH_H

#include <stdbool.h>
#include <stdint.h>

// Once, before the main loop: finds the GT911 on I2C2 and logs its product
// ID and its configuration's version, interrupt mode and report rate (read
// only; its configuration is never written).
void touch_init(void);

// On every pass of the main loop: when the controller's INT line is away
// from its idle level, reads its touch status and acknowledges it.
void touch_task(void);

// A finger is on the screen; false if touch_init() found no controller.
bool touch_down(void);

// How many times touch_task() has read the touch status since boot.
uint32_t touch_reads(void);

#endif
