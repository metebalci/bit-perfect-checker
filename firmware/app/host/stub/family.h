// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for a family's family.h, for the host tools: the chip's
// unique ID, which app/usb_formats.c reads for the serial number,
// FAMILY_CHECK, which app/stream.c uses, and the USB
// part that app/u2ps_glue.c uses.

#ifndef HOST_FAMILY_H
#define HOST_FAMILY_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern const uint32_t host_uid[3];

#define UID_BASE ((uintptr_t) host_uid)

// The host keeps the USB data and the class memory in plain memory
// (FAMILY_USB_DATA, FAMILY_CLASS_MEM; see families/stm32u5/family.h)
#define FAMILY_USB_DATA
#define FAMILY_CLASS_MEM

// The USB vector on; the host tool's own
void family_usb_irq_start(void);

// An invariant of app/stream.c that the host tools check (the firmware does
// not: FAMILY_CHECK there compiles to nothing); not assert(), which the
// tools' release build turns off
#define FAMILY_CHECK(x)                                                        \
  do {                                                                         \
    if (!(x)) {                                                                \
      fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #x);    \
      abort();                                                                 \
    }                                                                          \
  } while (0)

#endif
