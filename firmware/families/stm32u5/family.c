// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32U5 family support. The USB PHY set-up follows TinyUSB's
// hw/bsp/stm32u5 family files (MIT licence).

#include "family.h"

#include "board.h"
#include "console.h"
#include "load.h"
#include "log.h"
#include "tusb.h"
#include "usb_descriptors.h"

#define USB_DEVICE ((USB_OTG_DeviceTypeDef *) (USB_OTG_HS_BASE + USB_OTG_DEVICE_BASE))
#define USB_AUDIO_OUT                                                                                \
  ((USB_OTG_OUTEndpointTypeDef *) (USB_OTG_HS_BASE + USB_OTG_OUT_ENDPOINT_BASE                       \
                                   + (EP_AUDIO_OUT & 0x0Fu) * USB_OTG_EP_REG_SIZE))

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

void family_usb_init(uint32_t phy_clock_source, uint32_t phy_reference) {
  RCC_PeriphCLKInitTypeDef periph = {0};
  periph.PeriphClockSelection = RCC_PERIPHCLK_USBPHY;
  periph.UsbPhyClockSelection = phy_clock_source;
  HAL_RCCEx_PeriphCLKConfig(&periph);

  __HAL_RCC_SYSCFG_CLK_ENABLE();
  HAL_SYSCFG_SetOTGPHYReferenceClockSelection(phy_reference);

  // DM/DP. The OTG ID pin (PA10) is left alone: this is a device only.
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF10_USB;
  HAL_GPIO_Init(GPIOA, &gpio);

  __HAL_RCC_USB_OTG_HS_CLK_ENABLE();
  __HAL_RCC_USBPHYC_CLK_ENABLE();
  HAL_PWREx_EnableVddUSB();
  HAL_PWREx_EnableUSBHSTranceiverSupply();
  HAL_SYSCFG_EnableOTGPHY(SYSCFG_OTG_HS_PHY_ENABLE);

  // No VBUS sensing: the device always sees a valid session.
  tud_configure_dwc2_t cfg = CFG_TUD_CONFIGURE_DWC2_DEFAULT;
  cfg.vbus_sensing = false;
  tud_configure(0, TUD_CFGID_DWC2, &cfg);

  // USB first, then the log UART, then the tick
  NVIC_SetPriority(OTG_HS_IRQn, 1);
}

uint32_t board_usb_microframe(void) {
  return (USB_DEVICE->DSTS & USB_OTG_DSTS_FNSOF_Msk) >> USB_OTG_DSTS_FNSOF_Pos;
}

// DSTS and the audio OUT endpoint's DOEPCTL, read at the entry of the USB
// interrupt (OTG_HS_IRQHandler())
static uint32_t usb_entry_dsts;
static uint32_t usb_entry_doepctl;

void board_usb_entry(uint32_t *uframe, bool *odd) {
  *uframe = (usb_entry_dsts & USB_OTG_DSTS_FNSOF_Msk) >> USB_OTG_DSTS_FNSOF_Pos;
  *odd = (usb_entry_doepctl & USB_OTG_DOEPCTL_DPID_EONUM) != 0;
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

uint32_t tusb_time_millis_api(void) {
  return HAL_GetTick();
}

// Vector table entries, declared here for the startup file
void SysTick_Handler(void);
void OTG_HS_IRQHandler(void);
void HardFault_Handler(void);

void SysTick_Handler(void) {
  HAL_IncTick();
}

// The RX FIFO in words for the audio OUT endpoint's largest packet, as
// TinyUSB sizes it when the endpoint opens (dcd_dwc2.c,
// calc_device_grxfsiz(): 13 + 1 + 2 x (packet words + 1) + 2 x 9 endpoints).
#define USB_RX_FIFO_WORDS (13u + 1u + 2u * (UAC2_EP_OUT_SZ_4BYTE / 4u + 1u) + 2u * 9u)

void OTG_HS_IRQHandler(void) {
  uint32_t t0 = DWT->CYCCNT;
  usb_entry_dsts = USB_DEVICE->DSTS;
  usb_entry_doepctl = USB_AUDIO_OUT->DOEPCTL;
  bool reset = (USB_OTG_HS->GINTSTS & USB_OTG_GINTSTS_USBRST) != 0;
  tusb_int_handler(0, true);
  // TinyUSB's bus reset sizes the RX FIFO for EP0 alone (dfifo_device_init()).
  // With that size, after a bus reset within frames 0-255 of the host's
  // 2048-frame count, this controller NAKs the status stage of the next
  // control read until frame 256, although EP0 OUT is armed: up to 127 ms,
  // where USB 2.0 9.2.6.4 allows 50. Measured on the STM32U5A5 and U5G9,
  // in DMA and in slave mode; the STM32F723 does not do it. With the RX
  // FIFO kept at the audio endpoint's size from the reset on, it did not
  // happen. TinyUSB has just flushed the FIFO, and its TX FIFOs start at
  // the top of the FIFO RAM, far above this size.
  if (reset && USB_OTG_HS->GRXFSIZ < USB_RX_FIFO_WORDS) {
    USB_OTG_HS->GRXFSIZ = USB_RX_FIFO_WORDS;
  }
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
