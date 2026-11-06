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

// The double blink at Full-Speed: two flashes of DOUBLE_FLASH_MS,
// DOUBLE_GAP_MS apart, every DOUBLE_PERIOD_MS
#define DOUBLE_FLASH_MS  100u
#define DOUBLE_GAP_MS    150u
#define DOUBLE_PERIOD_MS 1200u

static bool double_blink(uint32_t now) {
  uint32_t t = now % DOUBLE_PERIOD_MS;
  uint32_t second = DOUBLE_FLASH_MS + DOUBLE_GAP_MS;
  return t < DOUBLE_FLASH_MS || (t >= second && t < second + DOUBLE_FLASH_MS);
}

void leds_task(void) {
  uint32_t now = board_millis();
  bool slow = ((now / 500) & 1) != 0;
  bool fast = ((now / 125) & 1) != 0;
  bool blue;
  bool green = false;
  bool red = false;
  // Set up at Full-Speed, where the device offers no audio
  bool full_speed = !status.streaming && status.usb != USB_NOT_MOUNTED && !status.high_speed;

  if (status.streaming) {
    blue = true;
  } else if (full_speed) {
    blue = double_blink(now);
  } else if (status.usb != USB_NOT_MOUNTED) {
    blue = slow;
  } else {
    blue = fast;
  }
  // The run's results, the first that holds; none at Full-Speed
  if (full_speed) {
    // green and red off
  } else if (status.run_fail) {
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
