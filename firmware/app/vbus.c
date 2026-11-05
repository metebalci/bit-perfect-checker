// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The user USB port's VBUS, debounced (vbus.h).

#include "vbus.h"

bool vbus_gone(vbus_t *v, bool level, uint32_t now) {
  if (!v->started || level != v->level) {
    v->started = true;
    v->level = level;
    v->since = now;
    return false;
  }
  if (now - v->since < VBUS_MS || (v->known && v->present == level)) {
    return false;
  }
  bool gone = v->known && v->present && !level;
  v->known = true;
  v->present = level;
  return gone;
}
