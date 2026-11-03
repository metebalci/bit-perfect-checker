// STM32U5 family support, shared by every STM32U5 board: the USB OTG HS
// controller with its internal PHY (STM32U59x/5Ax/5Fx/5Gx), the log UART,
// the tick, the cycle counter, the stack limit and the fault handler. A board's board_init() calls these.

#ifndef FAMILY_H
#define FAMILY_H

#include "stm32u5xx_hal.h"

// First in board_init(): the stack limit (MSPLIM) and the DWT cycle counter.
void family_core_init(void);

// USB OTG HS on PA11/PA12 with the internal HS PHY. phy_clock_source is the
// PHY's kernel clock (RCC_USBPHYCLKSOURCE_...), phy_reference its frequency
// (SYSCFG_OTG_HS_PHY_CLK_SELECT_...). Needs voltage range 1 or 2.
void family_usb_init(uint32_t phy_clock_source, uint32_t phy_reference);

// The log on a USART or LPUART with its pins and clock already set up by the
// board, clocked at clock_hz. The board's interrupt handler for that UART
// calls family_log_uart_irq().
void family_log_uart_init(USART_TypeDef *uart, IRQn_Type irq, uint32_t clock_hz, uint32_t baud);
void family_log_uart_irq(void);

#endif
