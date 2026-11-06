// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for a family's family.h, for the host tool usb_desc_tool.c:
// the chip's unique ID, which app/usb_descriptors.c reads for the serial
// number.

#ifndef HOST_FAMILY_H
#define HOST_FAMILY_H

#include <stdint.h>

extern const uint32_t host_uid[3];

#define UID_BASE ((uintptr_t) host_uid)

#endif
