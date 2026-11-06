// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The host port's VBUS switch (vbus_switch.h).

#include "vbus_switch.h"

uint8_t vbus_switch_refused(vbus_switch_t *s, uint32_t now) {
  s->state = VBUS_SWITCH_STATE_PRESENT;
  s->since = now;
  s->absent = false;
  return VBUS_SWITCH_FAULT;
}

// Switch on, unless VBUS is already there
static uint8_t try_on(vbus_switch_t *s, bool present, uint32_t now) {
  if (present) {
    return vbus_switch_refused(s, now);
  }
  s->state = VBUS_SWITCH_STATE_ON;
  return VBUS_SWITCH_ON;
}

uint8_t vbus_switch_start(vbus_switch_t *s, bool present, uint32_t now) {
  s->retries = 0;
  return try_on(s, present, now);
}

static uint8_t overcurrent_fault(vbus_switch_t *s, uint32_t now) {
  s->since = now;
  if (s->retries >= VBUS_SWITCH_RETRIES) {
    s->state = VBUS_SWITCH_STATE_OFF;
    return VBUS_SWITCH_FAULT | VBUS_SWITCH_OFF;
  }
  s->state = VBUS_SWITCH_STATE_OVERCURRENT;
  return VBUS_SWITCH_FAULT;
}

uint8_t vbus_switch_poll(vbus_switch_t *s, bool present, bool overcurrent, uint32_t now) {
  switch (s->state) {
    case VBUS_SWITCH_STATE_ON:
      // While on, the input sees the switch's own VBUS: only overcurrent counts
      return overcurrent ? overcurrent_fault(s, now) : 0u;
    case VBUS_SWITCH_STATE_PRESENT:
      // Logged once; on after VBUS_SWITCH_ABSENT_MS without VBUS
      if (present) {
        s->absent = false;
        return 0u;
      }
      if (!s->absent) {
        s->absent = true;
        s->since = now;
        return 0u;
      }
      return now - s->since >= VBUS_SWITCH_ABSENT_MS ? try_on(s, present, now) : 0u;
    case VBUS_SWITCH_STATE_OVERCURRENT:
      if (now - s->since < VBUS_SWITCH_RETRY_MS) {
        return 0u;
      }
      s->retries++;
      return try_on(s, present, now);
    default:
      return 0u;
  }
}

const char *vbus_switch_name(const vbus_switch_t *s) {
  switch (s->state) {
    case VBUS_SWITCH_STATE_ON: return "on";
    case VBUS_SWITCH_STATE_OFF: return "off";
    default: return "fault";
  }
}
