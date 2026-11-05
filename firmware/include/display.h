// What the firmware needs from a display. A board or shield with a display
// implements it and lists the sources in its board.cmake or shield.cmake;
// without one, the build links app/display_none.c.
//
// All three run in the main loop, whose passes the USB receive FIFO, a few
// milliseconds of audio, must not wait for. display_update() is called once
// at start-up and again after each change of the status: it takes the status
// and does not draw. display_task() is called on every pass and draws, a
// small part at a time (about 0.2 ms at most).

#ifndef DISPLAY_H
#define DISPLAY_H

#include "status.h"

void display_init(void);
void display_update(const status_t *status);
void display_task(void);

#endif
