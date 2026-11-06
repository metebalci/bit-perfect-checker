// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The host port's VBUS switch, as rules without hardware: when to switch it
// on, when a fault turns it off, and when it goes on again. Pure C: the main
// loop polls it with board_vbus() and board_vbus_overcurrent()
// (board_host.h) and does what it returns; the host tests run it
// (tests/test_vbus_switch.py).
//
// The switch goes on only while the port sees no VBUS. Two faults turn it
// off or keep it off ("vbus fault"):
// - VBUS already present: another host drives the cable, and switching on
//   would join two supplies. The fault is logged once; the switch goes on
//   once the port has seen no VBUS for VBUS_SWITCH_ABSENT_MS without a
//   break, however long that takes.
// - Overcurrent while the switch is on: it is tried again
//   VBUS_SWITCH_RETRY_MS later, at most VBUS_SWITCH_RETRIES times since the
//   start; after the last overcurrent it stays off until reset ("vbus off").

#ifndef VBUS_SWITCH_H
#define VBUS_SWITCH_H

#include <stdbool.h>
#include <stdint.h>

#define VBUS_SWITCH_RETRIES   3u     // tries after an overcurrent, since the start
#define VBUS_SWITCH_RETRY_MS  1000u  // from an overcurrent to the next try
#define VBUS_SWITCH_ABSENT_MS 1000u  // VBUS from elsewhere must be gone this long

// What the main loop does after a call, as flags, in this order
#define VBUS_SWITCH_FAULT 0x01u  // switch off, log "vbus fault"
#define VBUS_SWITCH_OFF   0x02u  // no more tries: log "vbus off"
#define VBUS_SWITCH_ON    0x04u  // switch on, log "vbus on"; if the board
                                 // refuses, call vbus_switch_refused()

typedef enum {
  VBUS_SWITCH_STATE_ON,           // switched on
  VBUS_SWITCH_STATE_PRESENT,      // off: VBUS from elsewhere, waiting until it has gone
  VBUS_SWITCH_STATE_OVERCURRENT,  // off after an overcurrent, a try pending
  VBUS_SWITCH_STATE_OFF,          // off until reset
} vbus_switch_state_t;

typedef struct {
  vbus_switch_state_t state;
  uint32_t retries;  // tries after an overcurrent so far
  uint32_t since;    // the overcurrent; or since when VBUS has been absent
  bool absent;       // VBUS_SWITCH_STATE_PRESENT: VBUS absent since `since`
} vbus_switch_t;

// At start: present is board_vbus(), now board_millis().
uint8_t vbus_switch_start(vbus_switch_t *s, bool present, uint32_t now);

// From the main loop: present as above, overcurrent the board's overcurrent
// input (active, or active since the last poll).
uint8_t vbus_switch_poll(vbus_switch_t *s, bool present, bool overcurrent, uint32_t now);

// The board refused to switch on, since VBUS appeared after the poll: as
// VBUS present.
uint8_t vbus_switch_refused(vbus_switch_t *s, uint32_t now);

// "on", "fault" (off after either fault, waiting) or "off" (until reset),
// for the status line.
const char *vbus_switch_name(const vbus_switch_t *s);

#endif
