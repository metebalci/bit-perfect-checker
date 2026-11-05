// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The user USB port's VBUS, as board_vbus() reads it, debounced: a level
// counts once it has held for VBUS_MS. The USB controller is not told about
// VBUS (TinyUSB forces the session valid), so without this a pulled cable
// looks like a host that suspended the bus. Pure C, no hardware: the main
// loop polls it, and the host tests run it (tests/test_vbus.py).

#ifndef VBUS_H
#define VBUS_H

#include <stdbool.h>
#include <stdint.h>

#define VBUS_MS 50u  // a level must hold this long; plugging bounces for less

typedef struct {
  bool started;    // a level was read
  bool level;      // the last level read
  uint32_t since;  // when it was first read
  bool known;      // a level has held VBUS_MS since boot
  bool present;    // the debounced level, once known
} vbus_t;

// level: board_vbus(); now: board_millis(). True once each time VBUS that
// was present has been absent for VBUS_MS. VBUS absent or present from boot
// gives nothing (the device starts not connected, and boot starts a run),
// and so does VBUS coming back.
bool vbus_gone(vbus_t *v, bool level, uint32_t now);

#endif
