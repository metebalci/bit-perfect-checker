// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// LED patterns from the status.

#include "leds.h"

#include <stddef.h>

#include "board.h"
#include "log.h"
#include "status.h"

void leds_test(void) {
  static const led_t order[LED_COUNT] = {LED_RED, LED_GREEN, LED_BLUE};
  for (int i = 0; i < LED_COUNT; i++) {
    const char *name = board_led_name(order[i]);
    if (name == NULL) {
      continue;
    }
    log_line("led %s", name);
    board_led(order[i], true);
    uint32_t t0 = board_millis();
    while (board_millis() - t0 < 300) {
    }
    board_led(order[i], false);
  }
}

void leds_task(void) {
  uint32_t now = board_millis();
  bool blink = ((now / 250) & 1) != 0;
  bool blue;
  bool green = false;
  bool red = false;

  if (status.streaming) {
    blue = true;
  } else if (status.usb != USB_NOT_MOUNTED) {
    blue = ((now / 500) & 1) != 0;
  } else {
    blue = ((now / 125) & 1) != 0;
  }
  // The run's results, the first that holds
  if (status.run_fail) {
    red = true;
  } else if (status.run_unknown) {
    red = blink;
  } else if (status.run_overrun) {
    red = green = blink;
  } else {
    green = status.run_pass;
  }
  board_led(LED_GREEN, green);
  board_led(LED_BLUE, blue);
  board_led(LED_RED, red);
}
