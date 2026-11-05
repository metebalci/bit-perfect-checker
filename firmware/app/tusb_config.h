// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// TinyUSB configuration: one UAC2 High-Speed audio sink with an explicit
// feedback endpoint, nothing else.

#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

#include "usb_descriptors.h"

// CFG_TUSB_MCU comes from the family (families/<family>/family.cmake).
#define CFG_TUSB_OS                 OPT_OS_NONE
#define CFG_TUSB_DEBUG              0

#define CFG_TUD_ENABLED             1
#define CFG_TUD_MAX_SPEED           OPT_MODE_HIGH_SPEED
#define CFG_TUD_ENDPOINT0_SIZE      64

// The DWC2 controller moves packets to memory itself (internal DMA) instead
// of the CPU reading its FIFO word by word in the interrupt, so the next
// isochronous OUT transfer is armed sooner after each packet.
#define CFG_TUD_DWC2_DMA_ENABLE     1

// The D-cache stays off on every MCU that has one (the STM32F7), so
// TinyUSB does no cache maintenance on the DMA buffers.
#define CFG_TUD_MEM_DCACHE_ENABLE   0

#define CFG_TUD_AUDIO               1
#define CFG_TUD_CDC                 0
#define CFG_TUD_MSC                 0
#define CFG_TUD_HID                 0
#define CFG_TUD_MIDI                0
#define CFG_TUD_VENDOR              0

// The clock source's RANGE answer lists all ten rates: 2 + 10 * 12 bytes.
#define CFG_TUD_AUDIO_CTRL_BUF_SZ   128

#define CFG_TUD_AUDIO_ENABLE_EP_OUT       1
#define CFG_TUD_AUDIO_ENABLE_FEEDBACK_EP  1

// Largest packet of any alt setting: 768 kHz, 4-byte slots, stereo.
#define CFG_TUD_AUDIO_FUNC_1_EP_OUT_SZ_MAX  UAC2_EP_OUT_SZ_4BYTE

// Receive FIFO between the USB interrupt and the main loop: 5.3 ms at
// 768 kHz with 4-byte slots. tu_fifo allows at most 32768 bytes; 32760 is a
// multiple of every frame size (4, 6 and 8 bytes), so that a FIFO that
// overwrites when full, as this one does, still hands out whole frames.
// stream.c counts every time it fills up.
#define CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ  32760

#endif
