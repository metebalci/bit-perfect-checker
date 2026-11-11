// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_usb_descriptors.py: the descriptors u2ps builds
// from the checker's description (app/usb_formats.c), read with
// GET_DESCRIPTOR requests through u2ps's PC test kit, the kit playing the
// host. Not part of the firmware.
//
//   usb_desc_tool [<formats>]  -> for high, then full: one line per
//                                 descriptor, "<speed> <name> <bytes in hex,
//                                 space-separated>"
//
// <formats> as the `formats` command takes them (16/2,24/4), all four by
// default. <name>: device, qualifier, configuration, other_speed, string0
// to string3. The kit's verdict line comes last; the exit code is its.

#include <stdio.h>

#include "u2ps_testkit_dev.h"
#include "usb_descriptors.h"

const uint32_t host_uid[3] = {0x00600015u, 0x39365006u, 0x20313148u};

// The device stack's callbacks: the description is all this tool needs
int u2ps_app_on_request(const u2ps_request_t *req) {
  (void) req;
  return U2PS_REQ_STALL;
}
int u2ps_app_on_sampling_frequency(uint32_t requested, uint32_t chosen, uint32_t id) {
  (void) requested;
  (void) chosen;
  (void) id;
  return 0;
}
void u2ps_app_on_configured(bool configured, u2ps_config_end_t why) {
  (void) configured;
  (void) why;
}
u2ps_stream_start_t u2ps_app_on_stream_start(const u2ps_stream_info_t *info, u2ps_stream_cfg_t *cfg) {
  (void) info;
  (void) cfg;
  return U2PS_STREAM_REFUSE;
}
void u2ps_app_on_packet(const uint8_t *data, uint16_t len, u2ps_uframe_t uframe) {
  (void) data;
  (void) len;
  (void) uframe;
}
void u2ps_app_on_hole(uint16_t uframes, u2ps_miss_t why) {
  (void) uframes;
  (void) why;
}
void u2ps_app_on_pause(uint16_t uframes) {
  (void) uframes;
}
void u2ps_app_on_stream_stop(void) {}
void u2ps_app_on_fault(u2ps_fault_t fault) {
  (void) fault;
}
void u2ps_app_on_reset(void) {}
void u2ps_app_on_suspend(void) {}
void u2ps_app_on_resume(void) {}
void u2ps_app_on_vbus(bool present) {
  (void) present;
}
void u2ps_app_on_control(const u2ps_setup_t *setup, int result) {
  (void) setup;
  (void) result;
}
void u2ps_app_on_contract(u2ps_contract_t violation) {
  (void) violation;
}

#define GET_DESCRIPTOR 6u

// One GET_DESCRIPTOR of the host, printed
static void get(const char *speed, const char *name, uint8_t type, uint8_t index, uint16_t lang) {
  static uint8_t buf[1024];
  u2ps_setup_t setup = {.bmRequestType = 0x80, .bRequest = GET_DESCRIPTOR,
                        .wValue = (uint16_t) (type << 8 | index), .wIndex = lang, .wLength = sizeof(buf)};
  uint16_t n = 0;
  U2PS_TESTKIT_CHECK(u2ps_testkit_dev_control(&setup, buf, &n) == U2PS_TESTKIT_CONTROL_OK);
  printf("%s %s", speed, name);
  for (uint16_t i = 0; i < n; i++) {
    printf(" %02x", buf[i]);
  }
  printf("\n");
}

int main(int argc, char **argv) {
  uac2_alt_t alts[UAC2_ALT_MAX];
  uint8_t n;
  if (!usb_formats_parse(argc > 1 ? argv[1] : UAC2_FORMATS_ALL, alts, &n)) {
    fprintf(stderr, "formats?\n");
    return 1;
  }
  usb_formats_set(alts, n);

  static const struct {
    u2ps_speed_t speed;
    const char *name;
  } speeds[] = {{U2PS_SPEED_HIGH, "high"}, {U2PS_SPEED_FULL, "full"}};
  for (size_t i = 0; i < 2; i++) {
    const char *s = speeds[i].name;
    u2ps_testkit_dev_reset();
    U2PS_TESTKIT_CHECK(u2ps_dev_init(usb_description()) == U2PS_DEV_OK);
    u2ps_dev_connect(true);
    u2ps_testkit_dev_vbus(true);
    U2PS_TESTKIT_CHECK(u2ps_testkit_dev_enumerate(speeds[i].speed) == U2PS_TESTKIT_CONTROL_OK);
    get(s, "device", 1, 0, 0);
    get(s, "qualifier", 6, 0, 0);
    get(s, "configuration", 2, 0, 0);
    get(s, "other_speed", 7, 0, 0);
    get(s, "string0", 3, 0, 0);
    for (uint8_t k = 1; k <= 3; k++) {
      char name[16];
      snprintf(name, sizeof(name), "string%u", (unsigned) k);
      get(s, name, 3, k, 0x0409);
    }
    u2ps_dev_connect(false);
  }
  return u2ps_testkit_result();
}
