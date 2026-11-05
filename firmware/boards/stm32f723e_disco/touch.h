// The STM32F723E-DISCO's touch panel as a second user button: a finger
// anywhere on the screen counts as a press of B1.

#ifndef TOUCH_H
#define TOUCH_H

#include <stdbool.h>

// In board_init(): resets the touch controller (10 ms) and lets it out of
// reset, so that it has started by the time touch_init() talks to it.
void touch_reset_release(void);

// Once, after touch_reset_release() and before the main loop: finds the
// controller (FT6x36 or FT3x67) on I2C3, sets its INT line to stay low
// while a finger is down, and logs what it found.
void touch_init(void);

// A finger is on the screen; false if touch_init() found no controller.
bool touch_down(void);

#endif
