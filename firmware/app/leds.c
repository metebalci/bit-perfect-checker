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

// The LED test, run by leds_task() so that the main loop, tud_task()
// included, keeps running while it lasts
#define TEST_MS 300u  // each LED's time

static const led_t test_order[LED_COUNT] = {LED_RED, LED_GREEN, LED_BLUE};
static int test_step = LED_COUNT;  // the lit LED's place in test_order; LED_COUNT: no test
static uint32_t test_since;

// From place i on: shows each LED on the display, and lights the first the
// board has
static void test_next(int i, uint32_t now) {
  for (; i < LED_COUNT; i++) {
    display_led_test(test_order[i]);  // also for an LED the board does not have
    const char *name = board_led_name(test_order[i]);
    if (name != NULL) {
      log_line("led %s", name);
      board_led(test_order[i], true);
      break;
    }
  }
  test_step = i;
  test_since = now;
}

void leds_test(void) {
  test_next(0, board_millis());
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
  if (test_step < LED_COUNT) {
    if (now - test_since < TEST_MS) {
      return;
    }
    board_led(test_order[test_step], false);
    test_next(test_step + 1, now);
    if (test_step < LED_COUNT) {
      return;
    }
  }
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
