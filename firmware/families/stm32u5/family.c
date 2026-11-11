// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32U5 family support. u2ps starts the USB PHY, its clocks and supplies
// (u2ps_dev_init()); the family sets the pins and the USB vector.

#include "family.h"

#include "board.h"
#include "console.h"
#include "load.h"
#include "log.h"
#include "u2ps_dwc2_dcd.h"

//--------------------------------------------------------------------+
// Core: stack limit, cycle counter
//--------------------------------------------------------------------+

extern uint32_t _sstack[];  // the lowest address of the stack (linker script)

void family_core_init(void) {
  // A stack that grows past its room raises a UsageFault (escalated to the
  // HardFault handler) instead of overwriting .bss.
  __set_MSPLIM((uint32_t) _sstack);

  DCB->DEMCR |= DCB_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

uint32_t board_cycles(void) {
  return DWT->CYCCNT;
}

uint32_t board_cpu_hz(void) {
  return SystemCoreClock;
}

//--------------------------------------------------------------------+
// USB
//--------------------------------------------------------------------+

void family_usb_pins(void) {
  // DM/DP. The OTG ID pin (PA10) is left alone: this is a device only.
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF10_USB;
  HAL_GPIO_Init(GPIOA, &gpio);
}

void family_usb_irq_start(void) {
  // USB first, then the log UART, then the tick
  NVIC_SetPriority(OTG_HS_IRQn, 1);
  NVIC_ClearPendingIRQ(OTG_HS_IRQn);
  NVIC_EnableIRQ(OTG_HS_IRQn);
}

//--------------------------------------------------------------------+
// Log UART: sends from the transmit-empty interrupt, passes received bytes
// to the console
//--------------------------------------------------------------------+

static USART_TypeDef *log_uart;

void family_log_uart_init(USART_TypeDef *uart, IRQn_Type irq, uint32_t clock_hz, uint32_t baud) {
  log_uart = uart;
  uart->CR1 = 0;
  uart->BRR = (clock_hz + baud / 2) / baud;
  uart->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE_RXFNEIE | USART_CR1_UE;
  NVIC_SetPriority(irq, 3);
  NVIC_EnableIRQ(irq);
}

void family_log_uart_irq(void) {
  uint32_t isr = log_uart->ISR;
  if ((isr & USART_ISR_RXNE_RXFNE) != 0) {
    console_rx_isr((uint8_t) log_uart->RDR);
  }
  // An overrun loses a byte and would keep the interrupt pending
  if ((isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE)) != 0) {
    log_uart->ICR = USART_ICR_ORECF | USART_ICR_FECF | USART_ICR_NECF;
  }
  if ((log_uart->CR1 & USART_CR1_TXEIE_TXFNFIE) != 0 && (isr & USART_ISR_TXE_TXFNF) != 0) {
    int c = log_tx_byte();
    if (c >= 0) {
      log_uart->TDR = (uint8_t) c;
    } else {
      log_uart->CR1 &= ~USART_CR1_TXEIE_TXFNFIE;
    }
  }
}

void board_log_start(void) {
  log_uart->CR1 |= USART_CR1_TXEIE_TXFNFIE;
}

//--------------------------------------------------------------------+
// Tick, interrupts and hooks
//--------------------------------------------------------------------+

void board_irq_disable(void) {
  __disable_irq();
}

void board_irq_enable(void) {
  __enable_irq();
}

uint32_t board_millis(void) {
  return HAL_GetTick();
}

// Vector table entries, declared here for the startup file
void SysTick_Handler(void);
void OTG_HS_IRQHandler(void);
void HardFault_Handler(void);

void SysTick_Handler(void) {
  HAL_IncTick();
}

// u2ps's handler; u2ps sizes the RX FIFO for its largest stream at every
// bus reset (u2ps_dwc2_fifo_setup())
void OTG_HS_IRQHandler(void) {
  uint32_t t0 = DWT->CYCCNT;
  u2ps_dwc2_dcd_irq();
  load_isr(DWT->CYCCNT - t0);
}

// A fault lights every LED and stops. A stack overflow enters with the stack
// pointer at MSPLIM, so the handler lifts the limit before it pushes
// anything; the few words it pushes below the stack's room are lost anyway.
void fault_stop(void);
__attribute__((naked)) void HardFault_Handler(void) {
  __asm volatile(
      "movs r0, #0\n"
      "msr msplim, r0\n"
      "b fault_stop\n");
}

__attribute__((weak)) void board_fault(void) {
}

void fault_stop(void) {
  board_fault();
  for (int i = 0; i < LED_COUNT; i++) {
    board_led((led_t) i, true);
  }
  while (1) {
  }
}

// Called by __libc_init_array; the startup files are not linked.
void _init(void);
void _init(void) {
}
