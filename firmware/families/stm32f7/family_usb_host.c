// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F7 family support for the BPC1 generator: USB OTG HS with its
// internal HS PHY as a USB host. It replaces the USB device part of family.c
// (built with FAMILY_USB_HOST). TinyUSB's DWC2 driver starts the HS PHY
// itself and forces host mode; the board switches VBUS (board_host.h).

#include "family.h"

#include "board.h"
#include "load.h"
#include "tusb.h"

#define USB_HOST ((USB_OTG_HostTypeDef *) (USB_OTG_HS_PERIPH_BASE + USB_OTG_HOST_BASE))

void family_usb_init(void) {
  // DM/DP (PB14/PB15) stay in their reset state, as for the device: the HS
  // PHY drives them. The OTG ID pin is left alone: TinyUSB forces host mode.
  __HAL_RCC_OTGPHYC_CLK_ENABLE();
  __HAL_RCC_USB_OTG_HS_CLK_ENABLE();
  __HAL_RCC_USB_OTG_HS_ULPI_CLK_ENABLE();

  // TinyUSB's host driver picks the HS PHY by itself, since the host is
  // High-Speed (CFG_TUH_MAX_SPEED, 0.21.0 hcd_dwc2.c:116)

  // USB first, then the log UART, then the tick
  NVIC_SetPriority(OTG_HS_IRQn, 1);
}

// HFNUM.FRNUM: the current (micro)frame's number, which advances with each
// SOF the host sends and wraps after 0x3FFF (RM0431 Rev 4 32.15.23)
uint32_t board_usb_microframe(void) {
  return (USB_HOST->HFNUM & USB_OTG_HFNUM_FRNUM_Msk) & 0x3FFFu;
}

void OTG_HS_IRQHandler(void);  // vector table entry
void OTG_HS_IRQHandler(void) {
  uint32_t t0 = DWT->CYCCNT;
  tusb_int_handler(BOARD_TUH_RHPORT, true);
  load_isr(DWT->CYCCNT - t0);
}
