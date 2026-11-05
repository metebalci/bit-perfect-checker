// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F7 family support. The USB set-up follows ST's STM32CubeF7 USB device
// examples for the STM32F723E-DISCO (BSD-3-Clause) and TinyUSB's
// hw/bsp/stm32f7 family files (MIT licence); TinyUSB's DWC2 driver starts the
// HS PHY itself.

#include "family.h"

#include "board.h"
#include "console.h"
#include "load.h"
#include "log.h"
#include "tusb.h"

#define USB_DEVICE ((USB_OTG_DeviceTypeDef *) (USB_OTG_HS_PERIPH_BASE + USB_OTG_DEVICE_BASE))

//--------------------------------------------------------------------+
// Core: caches, MPU, cycle counter
//--------------------------------------------------------------------+

// Linker script: the vector table, the USB interrupt's code (ITCM RAM, loaded
// from flash) and the USB buffers in DTCM.
extern uint32_t g_pfnVectors[];
extern uint32_t _sitcm[], _eitcm[], _siitcm[];
extern uint32_t _sdtcm_usb[], _edtcm_usb[];

void family_core_init(void) {
  // The image runs from the flash's ITCM interface (0x00200000), where code
  // and read-only data go through the ART accelerator; so does the vector
  // table.
  SCB->VTOR = (uint32_t) g_pfnVectors;
  __DSB();

  // The USB interrupt's code into ITCM RAM (zero wait states), before
  // anything calls it. memcpy is part of that code, and GCC turns a plain copy
  // loop into a call to memcpy: the volatile pointer prevents that.
  const uint32_t *src = _siitcm;
  for (volatile uint32_t *p = _sitcm; p < _eitcm; p++) {
    *p = *src++;
  }
  __DSB();
  __ISB();

  // The USB buffers and the receive FIFO's storage in DTCM are NOLOAD: zero
  // them as the startup code zeroes .bss.
  for (uint32_t *p = _sdtcm_usb; p < _edtcm_usb; p++) {
    *p = 0;
  }

  // The D-cache stays off, so the USB controller's DMA and the CPU always
  // see the same memory without cache maintenance.
  SCB_EnableICache();

  // ST's background region: 0x60000000-0xDFFFFFFF (FMC and QSPI, nothing
  // connected or set up) gets no access and no execution, so the core makes
  // no speculative reads there. Subregions 0-2 and 7 are disabled; there
  // the default memory map applies.
  MPU_Region_InitTypeDef mpu = {0};
  HAL_MPU_Disable();
  mpu.Enable = MPU_REGION_ENABLE;
  mpu.Number = MPU_REGION_NUMBER0;
  mpu.BaseAddress = 0x00000000;
  mpu.Size = MPU_REGION_SIZE_4GB;
  mpu.SubRegionDisable = 0x87;
  mpu.TypeExtField = MPU_TEX_LEVEL0;
  mpu.AccessPermission = MPU_REGION_NO_ACCESS;
  mpu.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  mpu.IsShareable = MPU_ACCESS_SHAREABLE;
  mpu.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  mpu.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;
  HAL_MPU_ConfigRegion(&mpu);

  // The first 32 bytes of ITCM RAM stay unused and get no access and no
  // execution, so that a NULL pointer read or a call through a NULL function
  // pointer faults instead of running the code placed in ITCM RAM.
  mpu.Number = MPU_REGION_NUMBER2;
  mpu.BaseAddress = 0x00000000;
  mpu.Size = MPU_REGION_SIZE_32B;
  mpu.SubRegionDisable = 0x00;
  HAL_MPU_ConfigRegion(&mpu);
  HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);

  // The Cortex-M7's DWT can ignore software writes until its CoreSight lock
  // access register is given the unlock key.
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->LAR = 0xC5ACCE55;
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

void family_usb_init(void) {
  // DM/DP (PB14/PB15) stay in their reset state, as in ST's examples: the
  // HS PHY drives them. The OTG ID and VBUS pins are left alone: this is a
  // device only, and TinyUSB forces the session valid.
  __HAL_RCC_OTGPHYC_CLK_ENABLE();
  __HAL_RCC_USB_OTG_HS_CLK_ENABLE();
  __HAL_RCC_USB_OTG_HS_ULPI_CLK_ENABLE();

  tud_configure_dwc2_t cfg = CFG_TUD_CONFIGURE_DWC2_DEFAULT;
  cfg.vbus_sensing = false;
  tud_configure(BOARD_TUD_RHPORT, TUD_CFGID_DWC2, &cfg);

  // USB first, then the log UART, then the tick
  NVIC_SetPriority(OTG_HS_IRQn, 1);
}

uint32_t board_usb_microframe(void) {
  return (USB_DEVICE->DSTS & USB_OTG_DSTS_FNSOF_Msk) >> USB_OTG_DSTS_FNSOF_Pos;
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
  uart->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE | USART_CR1_UE;
  NVIC_SetPriority(irq, 3);
  NVIC_EnableIRQ(irq);
}

void family_log_uart_irq(void) {
  uint32_t isr = log_uart->ISR;
  if ((isr & USART_ISR_RXNE) != 0) {
    console_rx_isr((uint8_t) log_uart->RDR);
  }
  // An overrun loses a byte and would keep the interrupt pending
  if ((isr & (USART_ISR_ORE | USART_ISR_FE | USART_ISR_NE)) != 0) {
    log_uart->ICR = USART_ICR_ORECF | USART_ICR_FECF | USART_ICR_NCF;
  }
  if ((log_uart->CR1 & USART_CR1_TXEIE) != 0 && (isr & USART_ISR_TXE) != 0) {
    int c = log_tx_byte();
    if (c >= 0) {
      log_uart->TDR = (uint8_t) c;
    } else {
      log_uart->CR1 &= ~USART_CR1_TXEIE;
    }
  }
}

void board_log_start(void) {
  log_uart->CR1 |= USART_CR1_TXEIE;
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
void MemManage_Handler(void);

void SysTick_Handler(void) {
  HAL_IncTick();
}

void OTG_HS_IRQHandler(void) {
  uint32_t t0 = DWT->CYCCNT;
  tusb_int_handler(BOARD_TUD_RHPORT, true);
  load_isr(DWT->CYCCNT - t0);
}

// A fault lights every LED and stops. The M7 has no stack limit register:
// the stack sits at the bottom of DTCM, so an overflow runs off the start of
// RAM instead of into data. The handler starts again from the top of the
// stack, since the stack pointer may be outside RAM.
void fault_stop(void);
__attribute__((naked)) void HardFault_Handler(void) {
  __asm volatile(
      "ldr r0, =_estack\n"
      "msr msp, r0\n"
      "b fault_stop\n");
}

// HAL_MPU_Enable enables the MemManage exception (SHCSR.MEMFAULTENA), so an
// MPU violation, such as a NULL pointer access, comes here instead of
// escalating to HardFault: the same handler.
void MemManage_Handler(void) __attribute__((alias("HardFault_Handler")));

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
