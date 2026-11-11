// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The formats the device offers, as alt settings, and the description u2ps
// builds every descriptor from: the device, its strings and the audio
// function with one alt setting per format.

#include <string.h>

#include "family.h"  // UID_BASE
#include "usb_descriptors.h"

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

uac2_alt_t uac2_alts[UAC2_ALT_MAX + 1];
uint8_t uac2_alt_count;

static const uac2_alt_t known[UAC2_ALT_MAX] = {{2, 16}, {4, 24}, {4, 32}, {3, 24}};

static bool format_known(uint8_t slot, uint8_t resolution) {
  for (size_t i = 0; i < UAC2_ALT_MAX; i++) {
    if (known[i].slot == slot && known[i].resolution == resolution) {
      return true;
    }
  }
  return false;
}

bool usb_formats_parse(const char *text, uac2_alt_t *alts, uint8_t *count) {
  uint8_t n = 0;
  const char *p = text;
  while (*p != '\0') {
    unsigned bits = 0;
    unsigned bytes = 0;
    while (*p >= '0' && *p <= '9' && bits < 100) {
      bits = bits * 10 + (unsigned) (*p++ - '0');
    }
    if (*p++ != '/') {
      return false;
    }
    while (*p >= '0' && *p <= '9' && bytes < 100) {
      bytes = bytes * 10 + (unsigned) (*p++ - '0');
    }
    if (*p == ',') {
      p++;
    } else if (*p != '\0') {
      return false;
    }
    if (n == UAC2_ALT_MAX || !format_known((uint8_t) bytes, (uint8_t) bits)) {
      return false;
    }
    for (uint8_t i = 0; i < n; i++) {
      if (alts[i].slot == bytes && alts[i].resolution == bits) {
        return false;
      }
    }
    alts[n++] = (uac2_alt_t) {(uint8_t) bytes, (uint8_t) bits};
  }
  if (n == 0) {
    return false;
  }
  *count = n;
  return true;
}

void usb_formats_set(const uac2_alt_t *alts, uint8_t count) {
  uac2_alts[0] = (uac2_alt_t) {0, 0};
  for (uint8_t i = 0; i < count; i++) {
    uac2_alts[i + 1] = alts[i];
  }
  uac2_alt_count = count;
}

bool usb_formats_equal(const uac2_alt_t *alts, uint8_t count) {
  if (count != uac2_alt_count) {
    return false;
  }
  for (uint8_t i = 0; i < count; i++) {
    if (alts[i].slot != uac2_alts[i + 1].slot || alts[i].resolution != uac2_alts[i + 1].resolution) {
      return false;
    }
  }
  return true;
}

void usb_formats_text(char *buf, size_t size) {
  size_t n = 0;
  buf[0] = '\0';
  for (uint8_t i = 1; i <= uac2_alt_count && n + 6 < size; i++) {
    unsigned bits = uac2_alts[i].resolution;
    if (i > 1) {
      buf[n++] = ',';
    }
    buf[n++] = (char) ('0' + bits / 10);
    buf[n++] = (char) ('0' + bits % 10);
    buf[n++] = '/';
    buf[n++] = (char) ('0' + uac2_alts[i].slot);
    buf[n] = '\0';
  }
}

//--------------------------------------------------------------------+
// The description
//--------------------------------------------------------------------+

static const uint32_t rates[] = {
  44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000,
};

// "bpc.metebalci.com:" and the 96-bit unique ID as 24 hex digits, made once:
// both descriptions point to it. A string descriptor holds at most 126
// characters (u2ps_dev_description_t).
#define SERIAL_PREFIX_LEN (sizeof USB_SERIAL_PREFIX - 1)
#define SERIAL_LEN        (SERIAL_PREFIX_LEN + 24)
_Static_assert(SERIAL_LEN <= 126, "USB_SERIAL_PREFIX too long for a string descriptor");
static char serial[SERIAL_LEN + 1];

static void serial_make(void) {
  static const char hex[] = "0123456789ABCDEF";
  const volatile uint32_t *uid = (const volatile uint32_t *) UID_BASE;
  memcpy(serial, USB_SERIAL_PREFIX, SERIAL_PREFIX_LEN);
  size_t n = SERIAL_PREFIX_LEN;
  for (int w = 0; w < 3; w++) {
    uint32_t v = uid[w];
    for (int i = 0; i < 8; i++) {
      serial[n++] = hex[(v >> 28) & 0xF];
      v <<= 4;
    }
  }
  serial[n] = '\0';
}

typedef struct {
  u2ps_dev_description_t desc;
  u2ps_format_t formats[UAC2_ALT_MAX];
} description_t;

static description_t descriptions[2];
static uint8_t description_next;  // the one not in use

const u2ps_dev_description_t *usb_description(void) {
  description_t *d = &descriptions[description_next];
  description_next ^= 1u;
  if (serial[0] == '\0') {
    serial_make();
  }
  for (uint8_t i = 0; i < uac2_alt_count; i++) {
    d->formats[i] = (u2ps_format_t) {.subslot_size = uac2_alts[i + 1].slot,
                                     .bit_resolution = uac2_alts[i + 1].resolution,
                                     .audio_data_format = U2PS_AUDIO_DATA_FORMAT_PCM};
  }
  d->desc = (u2ps_dev_description_t) {
    .id_vendor = USB_VID,
    .id_product = USB_PID,
    .bcd_device = USB_BCD,
    .manufacturer = "Mete Balci",
    .product = "Bit-Perfect Checker",
    .serial_number = serial,
    .self_powered = false,
    .max_power_ma = 100,
    .category = 0x08,         // I/O box (UAC2 A.7)
    .terminal_type = 0x0602,  // digital audio interface (Terminal Types 2.0, Table 2-6)
    .nr_channels = UAC2_CHANNELS,
    .channel_config = 0x3,    // front left, front right
    .formats = d->formats,
    .format_count = uac2_alt_count,
    .sampling_frequencies = rates,
    .sampling_frequency_count = ARRAY_SIZE(rates),
    .initial_sampling_frequency = USB_START_RATE,
  };
  return &d->desc;
}
