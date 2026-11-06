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
#include "vbus.h"

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

static vbus_t vbus;

// The D+ pull-up: off without VBUS (USB 2.0 7.1.5.1), and for a while when
// the formats change (vbus_hold_pullup()); on again when VBUS is back or the
// hold ends, and the host's reset and enumeration follow
static void pullup(bool on) {
  if (on) {
    uac2_attach();
    tud_connect();
  } else {
    tud_disconnect();
  }
}

// A pulled cable (vbus.h): uac2_unplug() ends the configuration, then a new
// run starts, as the user button does: a pull during playback cuts a file,
// and that result stays in the run before
static const vbus_ops_t vbus_ops = {.unplug = uac2_unplug, .run_start = stream_run_start, .pullup = pullup};

void vbus_hold_pullup(uint32_t ms) {
  vbus_act(vbus_hold(&vbus, board_millis(), ms), &vbus_ops);
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
  vbus_act(vbus_start(&vbus, board_vbus(), board_millis()), &vbus_ops);

  uint32_t shown = status.version - 1;  // show the initial status
  while (1) {
    load_loop();
    tud_task();
    stream_task();
    console_task();
    button_task();
    vbus_act(vbus_poll(&vbus, board_vbus(), board_millis()), &vbus_ops);
    leds_task();
    display_task();
    if (status.version != shown) {
      shown = status.version;
      display_update(&status);
    }
  }
}
