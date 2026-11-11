// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F7 family support, shared by every STM32F7 board: the USB OTG HS
// controller with its internal HS PHY (STM32F72x/F73x), the log UART, the
// tick, the caches and MPU, the cycle counter and the fault handler. A
// board's board_init() calls these.

#ifndef FAMILY_H
#define FAMILY_H

#include "stm32f7xx_hal.h"

// The USB interrupt's own code, the app's included, runs from ITCM RAM (zero
// wait states): a function marked FAMILY_ISR_CODE goes in .itcm_text, which
// the linker script places in ITCM RAM and family_core_init() copies there
// from flash. The families without ITCM RAM define it empty.
#define FAMILY_ISR_CODE __attribute__((section(".itcm_text")))

// The USB data the app keeps (FAMILY_USB_DATA) and the stream's class
// memory (FAMILY_CLASS_MEM) in DTCM, beside u2ps's own (.bss.u2ps_dma):
// zero wait states for the CPU, and the OTG HS DMA reaches it through the
// core's AHBS port; DTCM is not cached. The linker script places both and
// family_core_init() zeroes them.
#define FAMILY_USB_DATA __attribute__((section(".dtcm_usb")))
#define FAMILY_CLASS_MEM __attribute__((section(".dtcm_usb.class_mem")))

// First in board_init(): the I-cache (the D-cache stays off), the MPU's
// background region and the DWT cycle counter.
void family_core_init(void);

// The checker, after a successful u2ps_dev_init(): the USB vector's
// priority, above the log UART and the tick, and the vector on.
void family_usb_irq_start(void);

// The log on a USART with its pins and clock already set up by the board,
// clocked at clock_hz. The board's interrupt handler for that USART calls
// family_log_uart_irq().
void family_log_uart_init(USART_TypeDef *uart, IRQn_Type irq, uint32_t clock_hz, uint32_t baud);
void family_log_uart_irq(void);

#endif
