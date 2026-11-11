// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F723E-DISCO: STM32F723IEK6, 25 MHz oscillator into PH0 (HSE bypass),
// LD5 red PA7, LD6 green PB1, LD1 blue PA5 (through SB14), all active high,
// user button B1 on PA0 (high when pressed) and the touch panel as a second
// one, ST-LINK virtual COM port on USART6 PC6/PC7, USB OTG HS with the
// internal PHY on CN19 (micro-AB). The clock set-up follows ST's STM32CubeF7
// examples for this board, with HSE bypassed for the board's oscillator.

#include "board.h"

#include "family.h"
#include "log.h"
#include "touch.h"

typedef struct {
  GPIO_TypeDef *port;
  uint16_t pin;
  const char *name;
} led_pin_t;

static const led_pin_t leds[LED_COUNT] = {
  [LED_GREEN] = {GPIOB, GPIO_PIN_1, "green PB1"},
  [LED_BLUE]  = {GPIOA, GPIO_PIN_5, "blue PA5"},
  [LED_RED]   = {GPIOA, GPIO_PIN_7, "red PA7"},
};

// A failed clock set-up stops here with the LEDs off; the missing boot line
// on the log shows it.
static void halt(void) {
  __disable_irq();
  while (1) {
  }
}

static void clock_init(void) {
  RCC_OscInitTypeDef osc = {0};
  RCC_ClkInitTypeDef clk = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  // PLL: 25 MHz HSE / 25 * 432 / 2 = 216 MHz; / 9 = 48 MHz for the 48 MHz
  // domain
  osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  osc.HSEState = RCC_HSE_BYPASS;
  osc.PLL.PLLState = RCC_PLL_ON;
  osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  osc.PLL.PLLM = 25;
  osc.PLL.PLLN = 432;
  osc.PLL.PLLP = RCC_PLLP_DIV2;
  osc.PLL.PLLQ = 9;
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
    halt();
  }

  // 216 MHz needs the over-drive, switched on while no peripheral clock runs
  if (HAL_PWREx_EnableOverDrive() != HAL_OK) {
    halt();
  }

  clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
  clk.APB1CLKDivider = RCC_HCLK_DIV4;  // 54 MHz
  clk.APB2CLKDivider = RCC_HCLK_DIV2;  // 108 MHz
  if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_7) != HAL_OK) {
    halt();
  }
}

void board_init(void) {
  family_core_init();
  HAL_Init();  // 1 ms SysTick, re-timed by the clock set-up
  clock_init();

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();

  GPIO_InitTypeDef gpio = {0};
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  for (int i = 0; i < LED_COUNT; i++) {
    gpio.Pin = leds[i].pin;
    HAL_GPIO_WritePin(leds[i].port, leds[i].pin, GPIO_PIN_RESET);
    HAL_GPIO_Init(leds[i].port, &gpio);
  }

  // The OTG HS port's VBUS switch (U13, enable PH12, active high) starts
  // off. The checker is a device on that port and leaves it off.
  gpio.Pin = GPIO_PIN_12;
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_12, GPIO_PIN_RESET);
  HAL_GPIO_Init(GPIOH, &gpio);

  // User button B1: PA0, high when pressed (external pull-down on the board)
  gpio.Pin = GPIO_PIN_0;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLDOWN;
  HAL_GPIO_Init(GPIOA, &gpio);
  gpio.Pull = GPIO_NOPULL;

  // VBUS of CN19 on PB13 through R98 (0 ohm): 5 V from a host, read as a
  // GPIO. PB13 is 5 V tolerant (FT). R74 into T1's base (LD8, VBUS OK)
  // discharges VBUS when the cable is pulled, so no pull is needed.
  // UM2140 Rev 2 Figure 30; DS11853 Rev 10 Table 10 (PB13 FT).
  gpio.Pin = GPIO_PIN_13;
  gpio.Mode = GPIO_MODE_INPUT;
  HAL_GPIO_Init(GPIOB, &gpio);

  // The touch panel, the second user button: out of reset now, set up by
  // touch_init() from display_init()
  touch_reset_release();

  // Log: USART6 TX on PC6, RX on PC7, clocked from PCLK2
  gpio.Pin = GPIO_PIN_6 | GPIO_PIN_7;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Alternate = GPIO_AF8_USART6;
  HAL_GPIO_Init(GPIOC, &gpio);
  __HAL_RCC_USART6_CLK_ENABLE();
  family_log_uart_init(USART6, USART6_IRQn, HAL_RCC_GetPCLK2Freq(), LOG_BAUD);
}

void USART6_IRQHandler(void);  // vector table entry
void USART6_IRQHandler(void) {
  family_log_uart_irq();
}

void board_led(led_t led, bool on) {
  HAL_GPIO_WritePin(leds[led].port, leds[led].pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

const char *board_led_name(led_t led) {
  return leds[led].name;
}

// A fault: the display's backlight off (PH11, panel_st7789h2.c), so that the
// screen does not keep showing a result while the LEDs show the fault
void board_fault(void) {
  GPIOH->BSRR = (uint32_t) GPIO_PIN_11 << 16;
}

// B1, or a finger anywhere on the touch panel
bool board_button(void) {
  return HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_0) == GPIO_PIN_SET || touch_down();
}

bool board_vbus(void) {
  return HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_13) == GPIO_PIN_SET;
}
