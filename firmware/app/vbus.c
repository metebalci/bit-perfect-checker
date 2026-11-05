// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The user USB port's VBUS, debounced (vbus.h).

#include "vbus.h"

#include "log.h"

uint8_t vbus_start(vbus_t *v, bool level, uint32_t now) {
  v->level = level;
  v->since = now;
  v->known = false;
  v->present = false;
  v->connected = level;
  v->held = false;
  return level ? 0u : VBUS_DISCONNECT;
}

uint8_t vbus_poll(vbus_t *v, bool level, uint32_t now) {
  uint8_t act = 0u;
  if (level != v->level) {
    v->level = level;
    v->since = now;
  } else if (now - v->since >= VBUS_MS && !(v->known && v->present == level)) {
    if (!level && v->known && v->present) {
      act |= VBUS_GONE;
    }
    if (!level && v->connected) {
      act |= VBUS_DISCONNECT;
    }
    if (!level) {
      v->held = false;  // VBUS back connects, after its debounce
    }
    if (level && !v->connected && !v->held) {
      act |= VBUS_CONNECT;
    }
    v->known = true;
    v->present = level;
    v->connected = level && !v->held;
  }
  // The hold's end: the pull-up on once VBUS is there, also after a dip too
  // short to count
  if (v->held && level && (int32_t) (now - v->held_until) >= 0) {
    v->held = false;
    v->connected = true;
    act |= VBUS_CONNECT;
  }
  return act;
}

uint8_t vbus_hold(vbus_t *v, uint32_t now, uint32_t ms) {
  if (v->held) {
    v->held_until = now + ms;
    return 0u;
  }
  if (!v->connected) {
    return 0u;  // VBUS is gone: the pull-up comes on when it is back
  }
  v->held = true;
  v->held_until = now + ms;
  v->connected = false;
  return VBUS_DISCONNECT;
}

void vbus_act(uint8_t act, const vbus_ops_t *ops) {
  if (act & VBUS_GONE) {
    ops->unplug();
    log_line("usb disconnected");
    ops->run_start();
  }
  if (act & VBUS_DISCONNECT) {
    ops->pullup(false);
  }
  if (act & VBUS_CONNECT) {
    ops->pullup(true);
    log_line("usb connecting");
  }
}
