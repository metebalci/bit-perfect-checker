// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// LED patterns from the status.

#include "leds.h"

#include <stddef.h>

#include "board.h"
#include "display.h"
#include "log.h"
#include "status.h"

// For a display that does not show the LED test
__attribute__((weak)) void display_led_test(led_t led) {
  (void) led;
}

void leds_test(void) {
  static const led_t order[LED_COUNT] = {LED_RED, LED_GREEN, LED_BLUE};
  for (int i = 0; i < LED_COUNT; i++) {
    display_led_test(order[i]);  // also for an LED the board does not have
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
  bool slow = ((now / 500) & 1) != 0;
  bool fast = ((now / 125) & 1) != 0;
  bool blue;
  bool green = false;
  bool red = false;

  if (status.streaming) {
    blue = true;
  } else if (status.usb != USB_NOT_MOUNTED) {
    blue = slow;
  } else {
    blue = fast;
  }
  // The run's results, the first that holds
  if (status.run_fail) {
    red = true;
  } else if (status.run_unknown) {
    red = slow;
  } else if (status.run_overrun) {
    red = fast;
  } else {
    green = status.run_pass;
  }
  board_led(LED_GREEN, green);
  board_led(LED_BLUE, blue);
  board_led(LED_RED, red);
}
