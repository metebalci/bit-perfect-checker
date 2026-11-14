// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32U5 family support, shared by every STM32U5 board: the USB OTG HS
// controller with its internal PHY (STM32U59x/5Ax/5Fx/5Gx), the log UART,
// the tick, the cycle counter, the stack limit and the fault handler. A board's board_init() calls these.

#ifndef FAMILY_H
#define FAMILY_H

#include "stm32u5xx_hal.h"

// The USB data the app keeps (FAMILY_USB_DATA: the stream's queue) and the
// stream's class memory (FAMILY_CLASS_MEM) go in .noinit_usb, which the
// linker script places in SRAM1 to SRAM5, u2ps's DMA memory
// (u2ps_dwc2_dma_ok()), and which the startup code does not zero: about
// 1 MB, which zeroing before main() at the reset clock (4 MHz) would delay
// the attach by about half a second (USB 2.0 7.1.7.3 allows 100 ms). Data
// there must not rely on a zero start; both are written before they are
// read.
#define FAMILY_USB_DATA __attribute__((section(".noinit_usb")))
#define FAMILY_CLASS_MEM __attribute__((section(".noinit_usb.class_mem")))

// First in board_init(): the stack limit (MSPLIM) and the DWT cycle counter.
void family_core_init(void);

// USB OTG HS on PA11/PA12 with the internal HS PHY: the pins. u2ps_dev_init()
// starts the PHY from the HSE (U2PS_DWC2_HSE_HZ = HSE_VALUE), its supplies
// and the controller's clock; it needs voltage range 1 or 2 and the EPOD
// booster, which the board's clock set-up gives.
void family_usb_pins(void);

// After a successful u2ps_dev_init(): the USB vector's priority, above the
// log UART and the tick, and the vector on.
void family_usb_irq_start(void);

// The log on a USART or LPUART with its pins and clock already set up by the
// board, clocked at clock_hz. The board's interrupt handler for that UART
// calls family_log_uart_irq().
void family_log_uart_init(USART_TypeDef *uart, IRQn_Type irq, uint32_t clock_hz, uint32_t baud);
void family_log_uart_irq(void);

#endif
