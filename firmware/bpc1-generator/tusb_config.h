// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// TinyUSB configuration of the BPC1 generator: a High-Speed host for one
// device, with no hub and no class driver. Found before app/tusb_config.h,
// the checker's, since the generator's folder comes first on the include
// path.

#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

// CFG_TUSB_MCU, BOARD_TUH_RHPORT and CFG_TUH_MEM_SECTION come from the
// family (families/<family>/family.cmake).
#define CFG_TUSB_OS                 OPT_OS_NONE
#define CFG_TUSB_DEBUG              0

#define CFG_TUH_ENABLED             1
#define CFG_TUH_MAX_SPEED           OPT_MODE_HIGH_SPEED

// The DWC2 controller moves packets to and from memory itself (buffer DMA)
#define CFG_TUH_DWC2_DMA_ENABLE     1

// The D-cache stays off on every MCU that has one (the STM32F7), so
// TinyUSB does no cache maintenance on the DMA buffers.
#define CFG_TUH_MEM_DCACHE_ENABLE   0

// One device on the root port, no hub
#define CFG_TUH_HUB                 0
#define CFG_TUH_DEVICE_MAX          1

// The whole configuration descriptor must fit: a UAC2 device with several
// alt settings is well over the default 256 bytes.
#define CFG_TUH_ENUMERATION_BUFSIZE 2048

// Host channels 14 and 15 (of the OTG HS core's 16, RM0431 Rev 4 32.7.3)
// are the generator's own, for the isochronous OUT and feedback endpoints:
// TinyUSB neither allocates them nor handles their interrupts
// (lib/patches/tinyusb-host-reserved-channels.patch).
#define CFG_TUH_DWC2_CHANNEL_RESERVED 0xC000u

#endif
