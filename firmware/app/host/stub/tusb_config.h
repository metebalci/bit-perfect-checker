// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host stand-in for app/tusb_config.h, for the host tool stream_tool.c: what
// TinyUSB's common headers need, without a USB stack.

#ifndef HOST_TUSB_CONFIG_H
#define HOST_TUSB_CONFIG_H

#define CFG_TUSB_MCU    OPT_MCU_NONE
#define CFG_TUSB_OS     OPT_OS_NONE
#define CFG_TUSB_DEBUG  0
#define CFG_TUD_ENABLED 0
#define CFG_TUH_ENABLED 0

#endif
