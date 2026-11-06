// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator: a USB host on the board's user USB port, which is to
// play the BPC1 test signal to a UAC2 device such as the checker. So far it
// sets up the High-Speed host and the port's VBUS switch, and has the
// serial log with its commands; no class driver or isochronous code yet.

#include "board.h"
#include "board_host.h"
#include "console.h"
#include "display.h"
#include "generator.h"
#include "load.h"
#include "log.h"
#include "tusb.h"
#include "vbus_switch.h"

static vbus_switch_t vbus;
static bool mounted;

bool generator_usb_mounted(void) {
  return mounted;
}

const char *generator_vbus(void) {
  return vbus_switch_name(&vbus);
}

void tuh_mount_cb(uint8_t daddr) {
  (void) daddr;
  mounted = true;
}

void tuh_umount_cb(uint8_t daddr) {
  (void) daddr;
  mounted = false;
}

// What vbus_switch.c asks for; a switch-on the board refuses (VBUS appeared
// after the poll) counts as VBUS present
static void vbus_act(uint8_t act) {
  while (act != 0u) {
    uint8_t next = 0u;
    if ((act & VBUS_SWITCH_FAULT) != 0u) {
      board_vbus_switch_off();
      log_line("vbus fault");
    }
    if ((act & VBUS_SWITCH_OFF) != 0u) {
      log_line("vbus off");
    }
    if ((act & VBUS_SWITCH_ON) != 0u) {
      if (board_vbus_switch_on()) {
        log_line("vbus on");
      } else {
        next = vbus_switch_refused(&vbus, board_millis());
      }
    }
    act = next;
  }
}

int main(void) {
  board_init();
  board_host_init();
  console_boot();
  display_init();

  tusb_rhport_init_t host_init = {.role = TUSB_ROLE_HOST, .speed = TUSB_SPEED_HIGH};
  tusb_init(BOARD_TUH_RHPORT, &host_init);
  // VBUS once the host is set up, and only without VBUS from elsewhere
  vbus_act(vbus_switch_start(&vbus, board_vbus(), board_millis()));

  while (1) {
    load_loop();
    tuh_task();
    console_task();
    vbus_act(vbus_switch_poll(&vbus, board_vbus(), board_vbus_overcurrent(), board_millis()));
    display_task();
  }
}
