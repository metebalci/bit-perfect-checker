// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator's requests to the UAC2 device (uac2_control.h): one
// chain of asynchronous control transfers at a time, each started from the
// completion of the one before.
//
// After the mount: the language ID and the serial number string, the
// configuration descriptor (into a 2 KB buffer, then uac2_host_parse()), and
// the clock's RANGE of the sampling frequency, first its 2-byte count of
// subranges and then the whole reply, as Linux reads it. For a file:
// SET_CUR and GET_CUR of the sampling frequency on the clock source, then
// SET_INTERFACE to the alt setting; alt 0 after the file.
//
// Sources: UAC2 5.2.1 (RANGE), 5.2.2 (CUR), A.14 (request codes), A.17.1
// (the clock source's control selectors); USB 2.0 9.4.10 (SET_INTERFACE).

#include "uac2_control.h"

#include <string.h>

#include "log.h"
#include "tusb.h"

#define REQ_CUR             0x01u
#define REQ_RANGE           0x02u
#define CS_SAM_FREQ_CONTROL 0x01u
#define REQ_TYPE_CLASS_OUT  0x21u  // host to device, class, interface
#define REQ_TYPE_CLASS_IN   0xA1u  // device to host, class, interface
#define LANGID_DEFAULT      0x0409u  // English (United States)

#define SERIAL_MAX   126u  // UTF-16 code units of a string descriptor
#define CTL_BUF_SIZE 512u  // a string descriptor, a RANGE reply of UAC2_HOST_RANGE_MAX subranges
TU_VERIFY_STATIC(UAC2_HOST_RANGE_LEN(UAC2_HOST_RANGE_MAX) <= CTL_BUF_SIZE, "RANGE reply buffer");

// GEN_ENUM_CHECK (CMake ENUM_CHECK=ON): after each mount, set every rate of
// the device at 24 bits, three more formats at 48 and 176.4 kHz, and a rate
// it does not offer, which it must stall. For the enumeration step's
// hardware check; off in the release.
#ifndef GEN_ENUM_CHECK
#define GEN_ENUM_CHECK 0
#endif

typedef enum {
  STEP_LANGID,
  STEP_SERIAL_LEN,
  STEP_SERIAL,
  STEP_CONFIG,
  STEP_RANGE_LEN,
  STEP_RANGE,
  STEP_SET_CUR,
  STEP_GET_CUR,
  STEP_ALT,
  STEP_ALT0,
} step_t;

// The DWC2 moves control data with DMA: the buffers go where it can reach
CFG_TUH_MEM_SECTION CFG_TUH_MEM_ALIGN static uint8_t config_buf[CFG_TUH_ENUMERATION_BUFSIZE];
CFG_TUH_MEM_SECTION CFG_TUH_MEM_ALIGN static uint8_t ctl_buf[CTL_BUF_SIZE];

static struct {
  uac2_control_state_t state;
  uint8_t daddr;
  uint8_t gen;          // raised at each mount and unmount: older completions are ignored
  bool mounted_logged;  // the "usb mounted" line is out
  tusb_speed_t speed;
  uint16_t vid, pid;
  uint8_t serial_index;
  uint16_t langid;
  char serial[SERIAL_MAX + 1];
  const char *refused;  // the reason of REFUSED
  uac2_host_device_t dev;
  uint32_t rates;       // uac2_host_range()
  uint32_t rate;        // of the file being opened
  int alt;              // index into dev.alts: the file's alt setting
} c;

static void step(step_t s, bool ok, uint32_t len);

static uintptr_t tag(step_t s) {
  return (uintptr_t) c.gen << 8 | (uintptr_t) s;
}

static void done(tuh_xfer_t *xfer) {
  if (((xfer->user_data >> 8) & 0xFFu) != c.gen) {
    return;  // a transfer to a device that has gone since
  }
  step((step_t) (xfer->user_data & 0xFFu), xfer->result == XFER_RESULT_SUCCESS, xfer->actual_len);
}

// A class request on the clock source's sampling frequency control
static bool clock_request(uint8_t type, uint8_t request, uint16_t len, step_t s) {
  const tusb_control_request_t setup = {
    .bmRequestType = type,
    .bRequest = request,
    .wValue = tu_htole16((uint16_t) (CS_SAM_FREQ_CONTROL << 8)),
    .wIndex = tu_htole16((uint16_t) (c.dev.clock << 8 | c.dev.itf_ac)),
    .wLength = tu_htole16(len),
  };
  tuh_xfer_t xfer = {
    .daddr = c.daddr,
    .ep_addr = 0,
    .setup = &setup,
    .buffer = ctl_buf,
    .complete_cb = done,
    .user_data = tag(s),
  };
  return tuh_control_xfer(&xfer);
}

// Starts the request of step s; a request TinyUSB does not take completes
// at once as failed
static void start(step_t s) {
  bool sent;
  switch (s) {
    case STEP_LANGID:
      sent = tuh_descriptor_get_string_langid(c.daddr, ctl_buf, 4, done, tag(s));
      break;
    case STEP_SERIAL_LEN:
      sent = tuh_descriptor_get_string(c.daddr, c.serial_index, c.langid, ctl_buf, 2, done, tag(s));
      break;
    case STEP_SERIAL:
      sent = tuh_descriptor_get_string(c.daddr, c.serial_index, c.langid, ctl_buf, ctl_buf[0], done, tag(s));
      break;
    case STEP_CONFIG:
      sent = tuh_descriptor_get_configuration(c.daddr, 0, config_buf, sizeof(config_buf), done, tag(s));
      break;
    case STEP_RANGE_LEN:
      sent = clock_request(REQ_TYPE_CLASS_IN, REQ_RANGE, 2, s);
      break;
    case STEP_RANGE: {
      uint16_t n = tu_le16toh(tu_unaligned_read16(ctl_buf));
      if (n > UAC2_HOST_RANGE_MAX) {
        n = UAC2_HOST_RANGE_MAX;
      }
      sent = clock_request(REQ_TYPE_CLASS_IN, REQ_RANGE, (uint16_t) UAC2_HOST_RANGE_LEN(n), s);
      break;
    }
    case STEP_SET_CUR:
      tu_unaligned_write32(ctl_buf, tu_htole32(c.rate));
      sent = clock_request(REQ_TYPE_CLASS_OUT, REQ_CUR, 4, s);
      break;
    case STEP_GET_CUR:
      sent = clock_request(REQ_TYPE_CLASS_IN, REQ_CUR, 4, s);
      break;
    case STEP_ALT:
      sent = tuh_interface_set(c.daddr, c.dev.itf_as, c.dev.alts[c.alt].alt, done, tag(s));
      break;
    case STEP_ALT0:
      sent = tuh_interface_set(c.daddr, c.dev.itf_as, 0, done, tag(s));
      break;
    default:
      sent = false;
      break;
  }
  if (!sent) {
    step(s, false, 0);
  }
}

static const char *speed_name(tusb_speed_t speed) {
  return speed == TUSB_SPEED_HIGH ? "high" : (speed == TUSB_SPEED_LOW ? "low" : "full");
}

static void log_mounted(void) {
  log_line("usb mounted speed=%s usb=%04x:%04x serial=%s", speed_name(c.speed), c.vid, c.pid, c.serial);
}

static void log_device(void) {
  char text[UAC2_HOST_ALT_MAX * 6u + 1u];
  uac2_host_formats_text(&c.dev, text, sizeof(text));
  log_line("formats %s", text);
}

static void log_rates(void) {
  char text[UAC2_HOST_RATES * 7u + 1u];
  uac2_host_rates_text(c.rates, text, sizeof(text));
  log_line("rates %s", text);
}

static void refuse(const char *why) {
  c.refused = why;
  c.state = UAC2_CONTROL_REFUSED;
  log_line("error device=%s", why);
}

// The serial number string as printable ASCII
static void serial_text(const uint8_t *desc, uint32_t len) {
  uint32_t n = 0;
  if (len > desc[0]) {
    len = desc[0];
  }
  for (uint32_t i = 2; i + 1u < len && n < SERIAL_MAX; i += 2) {
    const uint16_t u = (uint16_t) (desc[i] | desc[i + 1u] << 8);
    c.serial[n++] = (u > 0x20u && u < 0x7Fu) ? (char) u : '?';
  }
  c.serial[n] = '\0';
}

// After the serial number string: the mount is logged, then the descriptor
static void mounted(void) {
  log_mounted();
  c.mounted_logged = true;
  if (c.speed != TUSB_SPEED_HIGH) {
    refuse(c.speed == TUSB_SPEED_LOW ? "low-speed" : "full-speed");
    return;
  }
  start(STEP_CONFIG);
}

static void step(step_t s, bool ok, uint32_t len) {
  switch (s) {
    case STEP_LANGID:
      // The first language ID, else English
      c.langid = (ok && len >= 4u && ctl_buf[0] >= 4u) ? tu_le16toh(tu_unaligned_read16(ctl_buf + 2)) : LANGID_DEFAULT;
      start(STEP_SERIAL_LEN);
      break;
    case STEP_SERIAL_LEN:
      if (ok && len == 2u && ctl_buf[0] > 2u) {
        start(STEP_SERIAL);
      } else {
        mounted();
      }
      break;
    case STEP_SERIAL:
      if (ok) {
        serial_text(ctl_buf, len);
      }
      mounted();
      break;
    case STEP_CONFIG: {
      if (!ok) {
        refuse("config");
        break;
      }
      const char *why = uac2_host_parse(config_buf, len, &c.dev);
      if (why != NULL) {
        refuse(why);
        break;
      }
      log_device();
      start(STEP_RANGE_LEN);
      break;
    }
    case STEP_RANGE_LEN:
      if (!ok || len < 2u || tu_le16toh(tu_unaligned_read16(ctl_buf)) == 0u) {
        refuse("range");
      } else {
        start(STEP_RANGE);
      }
      break;
    case STEP_RANGE:
      if (!ok || !uac2_host_range(ctl_buf, len, &c.rates)) {
        refuse("range");
      } else {
        log_rates();
        c.state = UAC2_CONTROL_READY;
      }
      break;
    case STEP_SET_CUR:
      if (ok) {
        start(STEP_GET_CUR);
      } else {
        log_line("rate unsupported=%lu", (unsigned long) c.rate);
        c.state = UAC2_CONTROL_READY;
      }
      break;
    case STEP_GET_CUR:
      if (ok && len == 4u && tu_le32toh(tu_unaligned_read32(ctl_buf)) == c.rate) {
        log_line("rate %lu", (unsigned long) c.rate);
        start(STEP_ALT);
      } else {
        log_line("rate unsupported=%lu", (unsigned long) c.rate);
        c.state = UAC2_CONTROL_READY;
      }
      break;
    case STEP_ALT:
      if (ok) {
        const uac2_host_alt_t *a = &c.dev.alts[c.alt];
        log_line("alt %u slot=%u res=%u", a->alt, a->slot, a->res);
        c.state = UAC2_CONTROL_OPEN;
      } else {
        log_line("error device=alt");
        c.state = UAC2_CONTROL_READY;
      }
      break;
    case STEP_ALT0:
      if (ok) {
        log_line("alt 0");
      } else {
        log_line("error device=alt");
      }
      c.state = UAC2_CONTROL_READY;
      break;
    default:
      break;
  }
}

#if GEN_ENUM_CHECK
static uint32_t check_next;  // the next case after a mount
#endif

void uac2_control_mount(uint8_t daddr) {
  tusb_desc_device_t desc;
  const uint8_t gen = c.gen;
  memset(&c, 0, sizeof(c));
  c.gen = (uint8_t) (gen + 1u);
  c.daddr = daddr;
  c.state = UAC2_CONTROL_SETUP;
  c.speed = tuh_speed_get(daddr);
  (void) tuh_vid_pid_get(daddr, &c.vid, &c.pid);
  c.serial_index = tuh_descriptor_get_device_local(daddr, &desc) ? desc.iSerialNumber : 0u;
#if GEN_ENUM_CHECK
  check_next = 0;
#endif
  if (c.serial_index != 0u) {
    start(STEP_LANGID);
  } else {
    mounted();
  }
}

void uac2_control_umount(uint8_t daddr) {
  if (daddr != c.daddr || c.state == UAC2_CONTROL_NONE) {
    return;
  }
  const bool logged = c.mounted_logged;
  const uint8_t gen = c.gen;
  memset(&c, 0, sizeof(c));
  c.gen = (uint8_t) (gen + 1u);
  if (logged) {
    log_line("usb disconnected");
  }
}

uac2_control_state_t uac2_control_state(void) {
  return c.state;
}

static bool open_file(uint32_t rate, uint8_t depth, bool dop, bool any_rate) {
  if (c.state != UAC2_CONTROL_READY) {
    return false;
  }
  bool offered = false;
  for (uint32_t i = 0; i < UAC2_HOST_RATES; i++) {
    if (((c.rates >> i) & 1u) != 0u && uac2_host_rate(i) == rate) {
      offered = true;
    }
  }
  if (!offered && !any_rate) {
    log_line("rate unsupported=%lu", (unsigned long) rate);
    return false;
  }
  const int alt = uac2_host_choose(&c.dev, rate, depth, dop);
  if (alt < 0) {
    log_line("error device=no-fit");
    return false;
  }
  c.rate = rate;
  c.alt = alt;
  c.state = UAC2_CONTROL_OPENING;
  // A clock whose frequency the host cannot set is only read back
  start(c.dev.clock_set ? STEP_SET_CUR : STEP_GET_CUR);
  return true;
}

bool uac2_control_open(uint32_t rate, uint8_t depth, bool dop) {
  return open_file(rate, depth, dop, false);
}

bool uac2_control_close(void) {
  if (c.state != UAC2_CONTROL_OPEN) {
    return false;
  }
  c.state = UAC2_CONTROL_CLOSING;
  start(STEP_ALT0);
  return true;
}

const uac2_host_device_t *uac2_control_device(void) {
  return &c.dev;
}

const uac2_host_alt_t *uac2_control_alt(void) {
  return c.state == UAC2_CONTROL_OPEN ? &c.dev.alts[c.alt] : NULL;
}

void uac2_control_show(void) {
  if (c.state == UAC2_CONTROL_NONE) {
    log_line("error device=not-connected");
    return;
  }
  if (!c.mounted_logged) {
    log_line("error device=not-ready");
    return;
  }
  log_mounted();
  if (c.state == UAC2_CONTROL_REFUSED) {
    log_line("error device=%s", c.refused);
    return;
  }
  if (c.dev.alt_count > 0u) {
    log_device();
  }
  if (c.state >= UAC2_CONTROL_READY) {
    log_rates();
  }
}

void uac2_control_task(void) {
#if GEN_ENUM_CHECK
  // Every offered rate at 24 bits; 16 and 32 bits at 48 kHz; DoP at
  // 176.4 kHz (DSD64); then 32 kHz, which no BPC1 file has, sent anyway
  static const struct {
    uint32_t rate;
    uint8_t depth;
    bool dop;
  } more[] = {{48000, 16, false}, {48000, 32, false}, {176400, 24, true}, {32000, 24, false}};

  if (c.state == UAC2_CONTROL_OPEN) {
    (void) uac2_control_close();
  } else if (c.state == UAC2_CONTROL_READY) {
    while (check_next < UAC2_HOST_RATES && ((c.rates >> check_next) & 1u) == 0u) {
      check_next++;
    }
    if (check_next < UAC2_HOST_RATES) {
      (void) open_file(uac2_host_rate(check_next++), 24, false, false);
    } else if (check_next < UAC2_HOST_RATES + TU_ARRAY_SIZE(more)) {
      const uint32_t k = check_next++ - UAC2_HOST_RATES;
      (void) open_file(more[k].rate, more[k].depth, more[k].dop, true);
    }
  }
#endif
}
