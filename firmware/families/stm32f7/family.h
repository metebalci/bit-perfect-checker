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

// First in board_init(): the I-cache (the D-cache stays off), the MPU's
// background region and the DWT cycle counter.
void family_core_init(void);

// USB OTG HS with the internal HS PHY on PB14/PB15, clocked from HSE, which
// must be one of the PHY's reference frequencies (HSE_VALUE). Needs AHB above
// 30 MHz.
void family_usb_init(void);

// The log on a USART with its pins and clock already set up by the board,
// clocked at clock_hz. The board's interrupt handler for that USART calls
// family_log_uart_irq().
void family_log_uart_init(USART_TypeDef *uart, IRQn_Type irq, uint32_t clock_hz, uint32_t baud);
void family_log_uart_irq(void);

#endif
