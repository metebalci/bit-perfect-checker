// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// bit-perfect-checker firmware, stage 1: a UAC2 High-Speed sink at every rate
// from 44.1 to 768 kHz that checks the BPC1 test signal it receives (the
// exact layer) and reports the verdict on the LEDs, a display if the board
// has one, and the serial log, which also takes commands for test
// automation.

#include "board.h"
#include "console.h"
#include "display.h"
#include "leds.h"
#include "load.h"
#include "status.h"
#include "stream.h"
#include "tusb.h"
#include "uac2.h"

status_t status;

#define BUTTON_MS 20u  // the button must hold its state this long

// A press of the user button starts a new run.
static void button_task(void) {
  static bool pressed;
  static bool raw;
  static uint32_t since;
  uint32_t now = board_millis();
  bool b = board_button();
  if (b != raw) {
    raw = b;
    since = now;
  } else if (b != pressed && now - since >= BUTTON_MS) {
    pressed = b;
    if (pressed) {
      stream_run_start();
    }
  }
}

int main(void) {
  board_init();
  status.allow_silence = ALLOW_SILENCE_DEFAULT;
  console_boot();
  stream_run_start();
  display_init();  // before the LED test, which the display's splash shows too
  leds_test();
  uac2_init();

  tusb_rhport_init_t dev_init = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_HIGH};
  tusb_init(BOARD_TUD_RHPORT, &dev_init);

  uint32_t shown = status.version - 1;  // show the initial status
  while (1) {
    load_loop();
    tud_task();
    stream_task();
    console_task();
    uac2_task();
    button_task();
    leds_task();
    display_task();
    if (status.version != shown) {
      shown = status.version;
      display_update(&status);
    }
  }
}
