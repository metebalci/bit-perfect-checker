// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator's requests to the UAC2 device: what it reads when a
// device is mounted, and the rate and alt setting it sets for each file.
// All transfers are TinyUSB's asynchronous control transfers; their
// completions run in tuh_task(), in the main loop, which also writes the
// log lines:
//
//   usb mounted speed=<high/full/low> usb=<vid>:<pid> serial=<text>
//                       a device is set up; serial= is empty without a serial
//                       number string, and characters other than printable
//                       ASCII read as '?'
//   formats <list>      its alt settings, "<res>/<slot>" in alt setting order
//   rates <list>        the BPC1 rates its clock's RANGE contains
//   error device=<why>  the device cannot play: uac2_host.h's reasons, or
//                       full-speed or low-speed, config (configuration descriptor not
//                       read), range (RANGE failed or held no subrange), alt
//                       (SET_INTERFACE failed), no-fit (no alt setting
//                       fits the file)
//   rate <Hz>           the rate is set: SET_CUR, then GET_CUR read it back
//   rate unsupported=<Hz>  not in the device's rates, SET_CUR stalled, or
//                       GET_CUR read back another rate
//   alt <n> slot=<bytes> res=<bits>, alt 0
//                       the alt setting selected; alt 0 between files
//   usb disconnected    the device is gone

#ifndef UAC2_CONTROL_H
#define UAC2_CONTROL_H

#include <stdbool.h>
#include <stdint.h>

#include "uac2_host.h"

typedef enum {
  UAC2_CONTROL_NONE,     // no device
  UAC2_CONTROL_SETUP,    // reading it after the mount
  UAC2_CONTROL_REFUSED,  // it cannot play (error device=)
  UAC2_CONTROL_READY,    // at alt 0, ready for a file
  UAC2_CONTROL_OPENING,  // setting the rate and the alt setting
  UAC2_CONTROL_OPEN,     // at the alt setting of the file
  UAC2_CONTROL_CLOSING,  // going back to alt 0
} uac2_control_state_t;

// From tuh_mount_cb() and tuh_umount_cb()
void uac2_control_mount(uint8_t daddr);
void uac2_control_umount(uint8_t daddr);

// From the main loop: runs the enumeration check of an ENUM_CHECK build.
void uac2_control_task(void);

uac2_control_state_t uac2_control_state(void);

// For a file of depth bits (DoP: 24) at rate, from READY: the rate (SET_CUR,
// GET_CUR), then the alt setting uac2_host_choose() picks (SET_INTERFACE).
// The state goes to OPENING, then OPEN, or back to READY after "rate
// unsupported=" or "error device=". False, with nothing sent, when not
// READY, or when the device does not have the rate or no alt setting fits
// (both logged).
bool uac2_control_open(uint32_t rate, uint8_t depth, bool dop);

// From OPEN (or OPENING once it is done): alt 0, then READY.
bool uac2_control_close(void);

// The device as parsed, valid from READY on; the alt setting of OPEN.
const uac2_host_device_t *uac2_control_device(void);
const uac2_host_alt_t *uac2_control_alt(void);

// The console's `device`: the device's lines again, "usb mounted ...", then
// "formats ..." and "rates ..." or its "error device=..."; without a device
// "error device=not-connected", and "error device=not-ready" before the
// "usb mounted" line is out.
void uac2_control_show(void);

#endif
