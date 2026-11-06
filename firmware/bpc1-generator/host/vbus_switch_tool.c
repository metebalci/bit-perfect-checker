// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_vbus_switch.py: runs bpc1-generator/vbus_switch.c
// on inputs given on stdin. Not part of the firmware.
//
//   <ms> <present 0|1> <overcurrent 0|1>
//                 a poll -> one line per action flag, in this order:
//                 "fault <ms>", "off <ms>", "on <ms>"
//   <ms> refuse   the board refused to switch on -> its action lines
//   <ms> state    -> "state on|fault|off"
//
// The first poll line goes to vbus_switch_start() (overcurrent ignored),
// the others to vbus_switch_poll(). Numbers in decimal; <ms> may wrap.

#include <stdio.h>
#include <string.h>

#include "vbus_switch.h"

static void show(uint8_t act, unsigned long ms) {
  if (act & VBUS_SWITCH_FAULT) {
    printf("fault %lu\n", ms);
  }
  if (act & VBUS_SWITCH_OFF) {
    printf("off %lu\n", ms);
  }
  if (act & VBUS_SWITCH_ON) {
    printf("on %lu\n", ms);
  }
}

int main(void) {
  vbus_switch_t s = {0};
  unsigned long ms;
  char word[16];
  bool first = true;
  while (scanf("%lu %15s", &ms, word) == 2) {
    if (strcmp(word, "state") == 0) {
      printf("state %s\n", vbus_switch_name(&s));
      continue;
    }
    if (strcmp(word, "refuse") == 0) {
      show(vbus_switch_refused(&s, (uint32_t) ms), ms);
      continue;
    }
    unsigned oc;
    if (scanf("%u", &oc) != 1) {
      return 1;
    }
    bool present = strcmp(word, "0") != 0;
    uint8_t act = first ? vbus_switch_start(&s, present, (uint32_t) ms)
                        : vbus_switch_poll(&s, present, oc != 0, (uint32_t) ms);
    first = false;
    show(act, ms);
  }
  return 0;
}
