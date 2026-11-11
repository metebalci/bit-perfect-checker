// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for a family's family.h, for the host tools: the chip's
// unique ID, which app/usb_formats.c reads for the serial number,
// FAMILY_ISR_CODE, which app/stream.c uses, and the USB part that
// app/u2ps_glue.c uses.

#ifndef HOST_FAMILY_H
#define HOST_FAMILY_H

#include <stdint.h>

extern const uint32_t host_uid[3];

#define UID_BASE ((uintptr_t) host_uid)

// The host has no ITCM RAM or DTCM (FAMILY_ISR_CODE, FAMILY_USB_DATA,
// FAMILY_CLASS_MEM; see families/stm32f7/family.h)
#define FAMILY_ISR_CODE
#define FAMILY_USB_DATA
#define FAMILY_CLASS_MEM

// The USB vector on; the host tool's own
void family_usb_irq_start(void);

#endif
