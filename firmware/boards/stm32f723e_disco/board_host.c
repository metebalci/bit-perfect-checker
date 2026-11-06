// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F723E-DISCO as a USB host on CN19 (the BPC1 generator). The port's
// VBUS switch U13 (STMPS2151STR) is enabled by PH12, active high, which
// board_init() sets up low. Its open-drain FAULT output goes to PH10, active
// low, pulled up on the board (R80) and lighting LD7 (red) while low. LD8
// (green) shows VBUS on the connector, which PB13 reads (board_vbus()):
// the switch's, or another host's. UM2140 Rev 2, 7.9 and Figure 30.

#include "board_host.h"

#include "board.h"
#include "family.h"

static volatile bool overcurrent;  // set by the interrupt, cleared by board_vbus_overcurrent()

void board_host_init(void) {
  // PH10: input with the interrupt on its falling edge (HAL_GPIO_Init
  // enables SYSCFG's clock and routes EXTI10 to port H). The internal
  // pull-up only adds to the board's.
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_10;
  gpio.Mode = GPIO_MODE_IT_FALLING;
  gpio.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOH, &gpio);
  EXTI->PR = EXTI_PR_PR10;
  // Above the USB interrupt: it only turns the switch off
  NVIC_SetPriority(EXTI15_10_IRQn, 0);
  NVIC_EnableIRQ(EXTI15_10_IRQn);
}

void EXTI15_10_IRQHandler(void);  // vector table entry
void EXTI15_10_IRQHandler(void) {
  if ((EXTI->PR & EXTI_PR_PR10) != 0) {
    GPIOH->BSRR = (uint32_t) GPIO_PIN_12 << 16;  // switch off
    EXTI->PR = EXTI_PR_PR10;
    overcurrent = true;
  }
}

bool board_vbus_switch_on(void) {
  // Never join another host's VBUS: read it right before switching on
  if (board_vbus()) {
    return false;
  }
  GPIOH->BSRR = GPIO_PIN_12;
  return true;
}

void board_vbus_switch_off(void) {
  GPIOH->BSRR = (uint32_t) GPIO_PIN_12 << 16;
}

bool board_vbus_overcurrent(void) {
  board_irq_disable();
  bool latched = overcurrent;
  overcurrent = false;
  board_irq_enable();
  return latched || HAL_GPIO_ReadPin(GPIOH, GPIO_PIN_10) == GPIO_PIN_RESET;
}
