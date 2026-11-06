// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_usb_descriptors.py: the descriptors that
// app/usb_descriptors.c returns at each speed, with TinyUSB's real headers.
// Not part of the firmware.
//
//   usb_desc_tool [<formats>]  -> for high, then full: one line per
//                                 descriptor, "<speed> <name> <bytes in hex,
//                                 space-separated>" or "<speed> <name> none"
//
// <formats> as the `formats` command takes them (16/2,24/4), all four by
// default. <name>: device, qualifier, configuration, other_speed.

#include <stdio.h>

#include "tusb.h"
#include "usb_descriptors.h"

const uint32_t host_uid[3] = {0x00600015u, 0x39365006u, 0x20313148u};

static tusb_speed_t speed;

tusb_speed_t tud_speed_get(void) {
  return speed;
}

static void print(const char *name, const uint8_t *desc, size_t len) {
  printf("%s %s", speed == TUSB_SPEED_HIGH ? "high" : "full", name);
  if (desc == NULL) {
    printf(" none\n");
    return;
  }
  for (size_t i = 0; i < len; i++) {
    printf(" %02x", desc[i]);
  }
  printf("\n");
}

static size_t config_len(const uint8_t *desc) {
  return desc == NULL ? 0 : (size_t) (desc[2] | desc[3] << 8);
}

int main(int argc, char **argv) {
  uac2_alt_t alts[UAC2_ALT_MAX];
  uint8_t n;
  if (!usb_formats_parse(argc > 1 ? argv[1] : UAC2_FORMATS_ALL, alts, &n)) {
    fprintf(stderr, "formats?\n");
    return 1;
  }
  usb_formats_set(alts, n);

  static const tusb_speed_t speeds[] = {TUSB_SPEED_HIGH, TUSB_SPEED_FULL};
  for (size_t i = 0; i < 2; i++) {
    speed = speeds[i];
    const uint8_t *d = tud_descriptor_device_cb();
    print("device", d, d == NULL ? 0 : d[0]);
    d = tud_descriptor_device_qualifier_cb();
    print("qualifier", d, d == NULL ? 0 : d[0]);
    d = tud_descriptor_configuration_cb(0);
    print("configuration", d, config_len(d));
    d = tud_descriptor_other_speed_configuration_cb(0);
    print("other_speed", d, config_len(d));
  }
  return 0;
}
