// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_leds.py: runs app/leds.c's start-up LED test and
// the LEDs after it on a clock it steps, one command per line on stdin. Not
// part of the firmware.
//
//   absent <led>       the board has no such LED (board_led_name() is NULL)
//   pass               the run's results are all BIT_PERFECT (green after the test)
//   fault              a USB fault (status.usb_fault)
//   test               leds_test() at the current time
//   run <ms> [<step>]  leds_task() every step ms (default 1) for ms
//
// Output, one line per event with its time in ms: "<ms> log <line>",
// "<ms> display <led>" (display_led_test()), and "<ms> leds <r><g><b>"
// with 0 or 1 for each LED, once the LEDs differ after a call. The clock
// moves only with run, so a leds_test() that waits for it never returns.
// <led> is red, green or blue.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "leds.h"
#include "log.h"
#include "status.h"

status_t status;

static uint32_t now_ms;
static bool led_on[LED_COUNT];
static bool shown[LED_COUNT];
static bool absent[LED_COUNT];
static const char *const names[LED_COUNT] = {[LED_RED] = "red", [LED_GREEN] = "green", [LED_BLUE] = "blue"};

uint32_t board_millis(void) {
  return now_ms;
}

void board_led(led_t led, bool on) {
  if (!absent[led]) {
    led_on[led] = on;
  }
}

const char *board_led_name(led_t led) {
  return absent[led] ? NULL : names[led];
}

void log_line(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  printf("%u log ", (unsigned) now_ms);
  vprintf(fmt, ap);
  printf("\n");
  va_end(ap);
}

void display_led_test(led_t led) {
  printf("%u display %s\n", (unsigned) now_ms, names[led]);
}

// The LEDs, when they changed
static void show(void) {
  if (memcmp(led_on, shown, sizeof(led_on)) != 0) {
    memcpy(shown, led_on, sizeof(led_on));
    printf("%u leds %d%d%d\n", (unsigned) now_ms, led_on[LED_RED], led_on[LED_GREEN], led_on[LED_BLUE]);
  }
}

static int led_by_name(const char *name) {
  for (int i = 0; i < LED_COUNT; i++) {
    if (strcmp(name, names[i]) == 0) {
      return i;
    }
  }
  return -1;
}

int main(void) {
  char line[128];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    char word[16];
    char arg[16];
    unsigned ms;
    unsigned step = 1;
    if (sscanf(line, "%15s", word) != 1) {
      continue;
    }
    if (strcmp(word, "absent") == 0 && sscanf(line, "absent %15s", arg) == 1 && led_by_name(arg) >= 0) {
      absent[led_by_name(arg)] = true;
    } else if (strcmp(word, "pass") == 0) {
      status.run_pass = true;
    } else if (strcmp(word, "fault") == 0) {
      status.usb_fault = true;
    } else if (strcmp(word, "test") == 0) {
      leds_test();
      show();
    } else if (strcmp(word, "run") == 0 && sscanf(line, "run %u %u", &ms, &step) >= 1 && step > 0) {
      for (uint32_t end = now_ms + ms; now_ms < end; now_ms += step) {
        leds_task();
        show();
      }
    } else {
      return 1;
    }
    fflush(stdout);
  }
  return 0;
}
