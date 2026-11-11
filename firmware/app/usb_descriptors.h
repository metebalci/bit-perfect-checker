// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// USB identity, the formats the device offers, and the description u2ps
// builds every descriptor from (usb_formats.c). The UAC2 topology: clock
// source -> input terminal (USB streaming) -> output terminal. There is no
// Feature Unit, so a host that wants to change the volume has to do it in
// software, where the checker sees it.

#ifndef USB_DESCRIPTORS_H
#define USB_DESCRIPTORS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "u2ps_dev.h"
#include "u2ps_dport_cfg.h"

// USB_VID, USB_PID and USB_SERIAL_PREFIX come from the build (CMake options
// of the same names). The default, 16C0:27E0, is Objective Development's
// shared VID:PID for audio class devices (V-USB's USB-IDs-for-free.txt): a
// device that uses it tells itself apart by its serial number string, which
// must start with a domain or e-mail address of its maker and a colon. The
// serial number is USB_SERIAL_PREFIX (ASCII) followed by the chip's unique ID
// as 24 hex digits.
#ifndef USB_VID
#define USB_VID   0x16C0
#endif
#ifndef USB_PID
#define USB_PID   0x27E0
#endif
#ifndef USB_SERIAL_PREFIX
#define USB_SERIAL_PREFIX "bpc.metebalci.com:"
#endif

// Raise with every change to the descriptors, so hosts that cache them notice.
#define USB_BCD   0x0104

// The interfaces u2ps gives the audio function: AudioControl, then
// AudioStreaming
enum {
  ITF_NUM_AUDIO_CONTROL = 0,
  ITF_NUM_AUDIO_STREAMING,
  ITF_NUM_TOTAL
};

#define UAC2_CHANNELS     2
#define UAC2_MAX_RATE     768000

// wMaxPacketSize: one frame more than the nominal number per microframe at
// the highest rate, as u2ps computes it for every data endpoint (u2ps_dev.h,
// u2ps_dev_description_t).
#define UAC2_EP_OUT_SZ(_slot) \
  ((((UAC2_MAX_RATE) + 7999) / 8000 + 1) * (_slot) * UAC2_CHANNELS)

// The most buffers a stream gets: the class memory (CLASS_MEM_BYTES, from
// the build) at the smallest stride, the 16/2 format's (u2ps_glue.c)
#define USB_BUFFERS_MAX (CLASS_MEM_BYTES / U2PS_DPORT_STRIDE(UAC2_EP_OUT_SZ(2)))

// A streaming format: slot size in bytes and bit resolution. The device
// knows four: 16/2, 24/4, 32/4 and 24/3 (bits/bytes), and offers some or all
// of them, in a chosen order, as alt settings 1 to uac2_alt_count.
typedef struct {
  uint8_t slot;
  uint8_t resolution;
} uac2_alt_t;

#define UAC2_ALT_MAX 4
#define UAC2_FORMATS_ALL "16/2,24/4,32/4,24/3"

extern uac2_alt_t uac2_alts[UAC2_ALT_MAX + 1];  // index 0 is the zero-bandwidth alt
extern uint8_t uac2_alt_count;

// Formats as text, "<bits>/<bytes>" separated by commas, e.g. "24/3,16/2".
// False for an unknown format, a repeated one, or none.
bool usb_formats_parse(const char *text, uac2_alt_t *alts, uint8_t *count);

// Offer these formats as alt settings 1 to count. Only while the device is
// disconnected, since the host reads the descriptors at enumeration.
void usb_formats_set(const uac2_alt_t *alts, uint8_t count);

bool usb_formats_equal(const uac2_alt_t *alts, uint8_t count);

// The formats offered now, as text.
void usb_formats_text(char *buf, size_t size);

// The rate the clock has before the host sets one. A host that finds the
// rate it wants there need not set it (Linux does not), so the stream and
// its checker start with it too.
#define USB_START_RATE 48000u

// The device's description for u2ps_dev_init(), with the formats offered
// now. u2ps keeps the description it was started with unchanged, so each
// call fills the other of two and returns it: the one in use stays valid
// until a re-init with the new one has returned.
const u2ps_dev_description_t *usb_description(void);

#endif
