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
  return level ? 0u : VBUS_DISCONNECT;
}

uint8_t vbus_poll(vbus_t *v, bool level, uint32_t now) {
  if (level != v->level) {
    v->level = level;
    v->since = now;
    return 0u;
  }
  if (now - v->since < VBUS_MS || (v->known && v->present == level)) {
    return 0u;
  }
  uint8_t act = 0u;
  if (!level && v->known && v->present) {
    act |= VBUS_GONE;
  }
  if (!level && v->connected) {
    act |= VBUS_DISCONNECT;
  }
  if (level && !v->connected) {
    act |= VBUS_CONNECT;
  }
  v->known = true;
  v->present = level;
  v->connected = level;
  return act;
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
