// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// For boards without a display.

#include "display.h"

void display_init(void) {
}

void display_update(const status_t *s) {
  (void) s;
}

void display_task(void) {
}

__attribute__((weak)) void display_wait(uint32_t ms) {
  uint32_t t0 = board_millis();
  while (board_millis() - t0 <= ms) {
  }
}
