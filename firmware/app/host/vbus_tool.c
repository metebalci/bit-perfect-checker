// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_vbus.py: runs app/vbus.c on VBUS levels given on
// stdin. Not part of the firmware.
//
//   <ms> <0|1>    -> "gone <ms>" when vbus_gone() returns true, else nothing
//
// Numbers in decimal; <ms> is board_millis() at the poll and may wrap.

#include <stdio.h>

#include "vbus.h"

int main(void) {
  vbus_t v = {0};
  unsigned long ms;
  int level;
  while (scanf("%lu %d", &ms, &level) == 2) {
    if (vbus_gone(&v, level != 0, (uint32_t) ms)) {
      printf("gone %lu\n", ms);
    }
  }
  return 0;
}
