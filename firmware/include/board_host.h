// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// What the BPC1 generator needs from a board on top of board.h: the user USB
// port as a host port, with its VBUS switch and the switch's overcurrent
// input. boards/<board>/board_host.c implements it; board_vbus() (board.h)
// reads the port's VBUS, the switch's own included.

#ifndef BOARD_HOST_H
#define BOARD_HOST_H

#include <stdbool.h>

// After board_init(), which keeps the switch off: the overcurrent input and
// its interrupt, which turns the switch off at once.
void board_host_init(void);

// Switches VBUS on, unless board_vbus() already sees VBUS (another host
// drives the cable): then false, and the switch stays off.
bool board_vbus_switch_on(void);

void board_vbus_switch_off(void);

// The switch reports overcurrent now, or did since the last call. The
// interrupt has turned the switch off already.
bool board_vbus_overcurrent(void);

#endif
