// What the firmware needs from a display. A board or shield with a display
// implements it and lists the sources in its board.cmake or shield.cmake;
// without one, the build links app/display_none.c.
//
// Both functions run in the main loop. display_update() is called once at
// start-up and again after each change of the status; it should return
// quickly, since the USB receive FIFO holds only a few milliseconds of audio.

#ifndef DISPLAY_H
#define DISPLAY_H

#include "status.h"

void display_init(void);
void display_update(const status_t *status);

#endif
