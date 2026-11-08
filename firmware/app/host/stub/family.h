// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for a family's family.h, for the host tools: the chip's
// unique ID, which app/usb_descriptors.c reads for the serial number, and
// FAMILY_ISR_CODE, which app/stream.c uses.

#ifndef HOST_FAMILY_H
#define HOST_FAMILY_H

#include <stdint.h>

extern const uint32_t host_uid[3];

#define UID_BASE ((uintptr_t) host_uid)

// The host has no ITCM RAM (FAMILY_ISR_CODE, see families/stm32f7/family.h)
#define FAMILY_ISR_CODE

#endif
