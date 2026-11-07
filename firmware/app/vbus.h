// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The user USB port's VBUS, as board_vbus() reads it, debounced: a level
// counts once it has held for VBUS_MS; and the D+ pull-up that follows it. The USB controller is not told about
// VBUS (TinyUSB forces the session valid), so without this a pulled cable
// looks like a host that suspended the bus. Pure C, no hardware: the main
// loop polls it, and the host tests run it (tests/test_vbus.py).

#ifndef VBUS_H
#define VBUS_H

#include <stdbool.h>
#include <stdint.h>

#define VBUS_MS 50u  // a level must hold this long; plugging bounces for less

// What the main loop does after a call, as flags, in this order
#define VBUS_GONE       0x01u  // VBUS that was present has been absent for VBUS_MS: unplugged
#define VBUS_DISCONNECT 0x02u  // D+ pull-up off (tud_disconnect())
#define VBUS_CONNECT    0x04u  // D+ pull-up on (tud_connect())

typedef struct {
  bool level;      // the last level read
  uint32_t since;  // when it was first read
  bool known;      // a level has held VBUS_MS since start
  bool present;    // the debounced level, once known
  bool connected;  // the pull-up is on
  bool held;       // vbus_hold(): the pull-up off until held_until
  uint32_t held_until;
} vbus_t;

// What vbus_act() does, from main.c (or the host tests' stand-ins)
typedef struct {
  void (*unplug)(void);     // end the configuration at once, its lines included, without logging the disconnect
  void (*run_start)(void);  // a new run, as the user button starts one
  void (*pullup)(bool on);  // the D+ pull-up: tud_connect() or tud_disconnect()
} vbus_ops_t;

// At start, after tusb_init(), which turns the pull-up on: VBUS_CONNECT when
// VBUS is present, so that the attach is logged; VBUS_DISCONNECT when it is
// absent, since a device must not pull D+ up without VBUS (USB 2.0 7.1.5.1).
uint8_t vbus_start(vbus_t *v, bool level, uint32_t now);

// level: board_vbus(); now: board_millis(). VBUS_GONE once each time VBUS
// that was present has been absent for VBUS_MS, with VBUS_DISCONNECT;
// VBUS_CONNECT when VBUS has been present for VBUS_MS with the pull-up off,
// or when a hold has ended and VBUS is present at the poll.
uint8_t vbus_poll(vbus_t *v, bool level, uint32_t now);

// A change of formats (uac2.c): the pull-up off now, and on again ms later
// once VBUS is present (also after a dip too short to count), so that the
// host enumerates the device again. VBUS_DISCONNECT when the pull-up was
// on. With VBUS gone the pull-up is off already, and comes on when VBUS is
// back for VBUS_MS; a pull during the hold ends it the same way. A hold
// during a hold moves its end.
uint8_t vbus_hold(vbus_t *v, uint32_t now, uint32_t ms);

// main.c: vbus_hold() on the user port's VBUS, then vbus_act() on its flags
void vbus_hold_pullup(uint32_t ms);

// Acts on vbus_start()'s or vbus_poll()'s flags, in this order. A pull:
// the configuration ends (a stream's lines first), "usb disconnected" is
// logged, whatever TinyUSB's state (a host may reset the bus as the plug
// leaves, so the device is no longer set up when VBUS goes), a new run
// starts, and the pull-up goes off. VBUS at start or back, or a hold's end:
// the pull-up goes on and "usb connecting" is logged.
void vbus_act(uint8_t act, const vbus_ops_t *ops);

#endif
