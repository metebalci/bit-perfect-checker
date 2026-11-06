// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator's view of a USB Audio Class 2 device: its configuration
// descriptor parsed into what playing needs, the clock's RANGE reply read as
// the BPC1 rates it accepts, and the alt setting chosen for a file. Portable
// C99, no TinyUSB and no hardware: tests/test_uac2_parse.py compiles it on
// the host. The requests themselves are in uac2_control.c.
//
// Sources: USB 2.0 (9.6), Audio Devices 2.0 (UAC2, 4.7 to 4.10, 5.2) and
// Audio Data Formats 2.0 (2.3.1).

#ifndef UAC2_HOST_H
#define UAC2_HOST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UAC2_HOST_ALT_MAX   8u   // streaming alt settings besides alt 0
#define UAC2_HOST_RANGE_MAX 32u  // subranges read from a RANGE reply
#define UAC2_HOST_RATES     10u  // the BPC1 rates, uac2_host_rate()

// RANGE of a 4-byte control: wNumSubRanges, then dMIN, dMAX, dRES per
// subrange (UAC2 5.2.1, Table 5-7)
#define UAC2_HOST_RANGE_LEN(_n) (2u + 12u * (_n))

typedef enum {
  UAC2_SYNC_NONE = 0,  // bmAttributes bits 3:2 of the data endpoint
  UAC2_SYNC_ASYNC,
  UAC2_SYNC_ADAPTIVE,
  UAC2_SYNC_SYNC,
} uac2_sync_t;

// A streaming alt setting: one stereo Type I PCM format
typedef struct {
  uint8_t alt;          // bAlternateSetting
  uint8_t slot;         // bSubslotSize, bytes
  uint8_t res;          // bBitResolution, bits
  uint8_t sync;         // uac2_sync_t of the data endpoint
  uint8_t ep_out;       // data endpoint address
  uint8_t ep_fb;        // explicit feedback endpoint address, 0 without
  uint16_t out_size;    // the data endpoint's wMaxPacketSize, bytes
  uint16_t fb_size;
  uint8_t fb_interval;  // bInterval: polled every 2^(bInterval-1) microframes
} uac2_host_alt_t;

// The audio function that plays: the USB streaming input terminal, its
// clock source, and the streaming interface linked to it
typedef struct {
  uint8_t itf_ac;       // AudioControl interface
  uint8_t itf_as;       // AudioStreaming interface
  uint8_t clock;        // clock source entity, the target of the rate requests
  bool clock_set;       // its frequency control is host-programmable
  uint8_t terminal;     // the USB streaming input terminal
  uint8_t alt_count;
  uac2_host_alt_t alts[UAC2_HOST_ALT_MAX];  // in alt setting order
} uac2_host_device_t;

// Parses a configuration descriptor of len bytes. NULL when the device can
// play, else why not, as one word for "error device=<reason>":
//   cut                the descriptor ends early: wTotalLength beyond len,
//                      or a descriptor beyond wTotalLength
//   length             a descriptor with bLength below 2, or too short for
//                      its type
//   not-config         not a configuration descriptor
//   not-audio          no audio interface
//   uac1               an audio function of USB Audio Class 1.0
//   version            bcdADC other than 2.00
//   clock              the streaming terminal's clock is no clock source
//                      (or a clock selector with one input), or missing
//   no-terminal        no USB streaming input terminal
//   no-streaming       no streaming interface linked to that terminal, or
//                      it has no alt 0 without endpoints
//   no-alt             no alt setting with a format
//   too-many-alts      more than UAC2_HOST_ALT_MAX
//   alt-repeated       one alt setting twice
//   format             a format other than Type I PCM, or a slot or
//                      resolution that do not fit together
//   channels           a format with other than 2 channels
//   endpoint           no isochronous OUT data endpoint, or a feedback
//                      endpoint that is not isochronous IN of at least
//                      4 bytes
//   high-bandwidth     more than one transaction per microframe
//   interval           a data endpoint not served every microframe
//   implicit-feedback  an asynchronous endpoint without explicit feedback
const char *uac2_host_parse(const uint8_t *desc, size_t len, uac2_host_device_t *dev);

// The BPC1 rates, i from 0 to UAC2_HOST_RATES - 1: 44100 to 768000.
uint32_t uac2_host_rate(uint32_t i);

// Reads a RANGE reply of len bytes: bit i set for each BPC1 rate i inside
// a subrange. A subrange takes MIN, MIN + RES, ... up to MAX, and only MIN
// when RES is 0, as Linux reads it (sound/usb/format.c). Subranges beyond
// len or UAC2_HOST_RANGE_MAX are left out. False when the reply holds no
// whole subrange.
bool uac2_host_range(const uint8_t *range, size_t len, uint32_t *rates);

// The alt setting for a file of `depth` bits per sample (DoP: 24) at
// `rate`, as an index into dev->alts, or -1 if none fits: the smallest slot
// that holds the depth, with a resolution at least the depth (DoP: a slot
// of 3 or 4 bytes), whose wMaxPacketSize holds a packet of the nominal
// frames rounded up plus one; among equal slots, the first alt setting.
int uac2_host_choose(const uac2_host_device_t *dev, uint32_t rate, uint8_t depth, bool dop);

// The bytes of the largest packet sent at rate in the slot: the frames of a
// microframe rounded up, plus one, in two channels
uint32_t uac2_host_packet_max(uint32_t rate, uint8_t slot);

// The formats as "<res>/<slot>,..." in alt setting order, as the checker
// writes them; truncated to size.
void uac2_host_formats_text(const uac2_host_device_t *dev, char *buf, size_t size);

// The rates of a uac2_host_range() mask as "<Hz>,...", truncated to size.
void uac2_host_rates_text(uint32_t rates, char *buf, size_t size);

#endif
