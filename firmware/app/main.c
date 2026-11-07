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

// The waits of display_init() (display.h): the device is attached by then,
// so the USB stack and the VBUS debounce run meanwhile, and the host can
// enumerate the device. At least ms, as HAL_Delay() waits.
void display_wait(uint32_t ms) {
  uint32_t t0 = board_millis();
  while (board_millis() - t0 <= ms) {
    tud_task();
    vbus_act(vbus_poll(&vbus, board_vbus(), board_millis()), &vbus_ops);
  }
}

int main(void) {
  board_init();
  status.allow_silence = ALLOW_SILENCE_DEFAULT;
  console_boot();
  stream_run_start();
  uac2_init();

  // The D+ pull-up first: with VBUS present, within 100 ms of reset (USB 2.0
  // 7.1.7.3, TSIGATT). The display's start-up and the LED test come after
  // it, and both keep tud_task() running, so the host can reset and
  // enumerate the device meanwhile.
  tusb_rhport_init_t dev_init = {.role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_HIGH};
  tusb_init(BOARD_TUD_RHPORT, &dev_init);
  vbus_act(vbus_start(&vbus, board_vbus(), board_millis()), &vbus_ops);
  display_init();  // before the LED test, which the display's splash shows too
  leds_test();     // started: leds_task() runs it

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
