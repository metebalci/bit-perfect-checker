// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Copyright (c) 2023 Ha Thach (tinyusb.org)
// NUCLEO-U5A5ZJ-Q: STM32U5A5ZJT6Q (SMPS), 16 MHz HSE crystal, LD1 green PC7,
// LD2 blue PB7, LD3 red PG2, user button B1 on PC13 (high when pressed),
// ST-LINK virtual COM port on USART1 PA9/PA10, user USB-C on the OTG HS PHY.
// The clock set-up follows TinyUSB's board file for this board; see the
// notice at clock_init() below.

#include "board.h"

#include "family.h"
#include "log.h"
#include "stm32u5xx_ll_adc.h"

typedef struct {
  GPIO_TypeDef *port;
  uint16_t pin;
  const char *name;
} led_pin_t;

static const led_pin_t leds[LED_COUNT] = {
  [LED_GREEN] = {GPIOC, GPIO_PIN_7, "green PC7"},
  [LED_BLUE]  = {GPIOB, GPIO_PIN_7, "blue PB7"},
  [LED_RED]   = {GPIOG, GPIO_PIN_2, "red PG2"},
};

// A failed clock set-up stops here with the LEDs off; the missing boot line
// on the log shows it.
static void halt(void) {
  __disable_irq();
  while (1) {
  }
}

/*
 * clock_init() is adapted from SystemClock_Config() in TinyUSB's
 * hw/bsp/stm32u5/boards/stm32u5a5nucleo/board.h:
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2023, Ha Thach (tinyusb.org)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
static void clock_init(void) {
  RCC_OscInitTypeDef osc = {0};
  RCC_ClkInitTypeDef clk = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  if (HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1) != HAL_OK) {
    halt();
  }

  // PLL1: 16 MHz HSE / 1 * 20 / 2 = 160 MHz
  osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  osc.HSEState = RCC_HSE_ON;
  osc.PLL.PLLState = RCC_PLL_ON;
  osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  osc.PLL.PLLMBOOST = RCC_PLLMBOOST_DIV1;
  osc.PLL.PLLM = 1;
  osc.PLL.PLLN = 20;
  osc.PLL.PLLP = 8;
  osc.PLL.PLLQ = 2;
  osc.PLL.PLLR = 2;
  osc.PLL.PLLRGE = RCC_PLLVCIRANGE_1;
  osc.PLL.PLLFRACN = 0;
  if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
    halt();
  }

  clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1
                  | RCC_CLOCKTYPE_PCLK2 | RCC_CLOCKTYPE_PCLK3;
  clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
  clk.APB1CLKDivider = RCC_HCLK_DIV1;
  clk.APB2CLKDivider = RCC_HCLK_DIV1;
  clk.APB3CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_4) != HAL_OK) {
    halt();
  }

  // Port G pins 2 to 15 (the red LED) are powered from VDDIO2
  HAL_PWREx_EnableVddIO2();

  if (HAL_PWREx_ConfigSupply(PWR_SMPS_SUPPLY) != HAL_OK) {
    halt();
  }
}

// VBUS of the user USB-C port (CN15) on PC2, VBUS_SENSE: VBUS divided by
// 7.61 (ST's BSP, stm32u5xx_nucleo_usbpd_pwr.c: R34 49.9k of 379.9k), so
// 0.66 V at 5 V, below a GPIO's input high level: read with ADC1 (IN3),
// converting continuously. UM2861 Rev 5 Table 13 (PC2 VBUS_SENSE).
#define VBUS_DIVIDER_X100 761u
#define VBUS_MIN_MV       3500u  // present at or above
#define VDDA_MV           3300u
#define ADC_FULL          16384u  // 14 bits
#define VBUS_MIN_CODE ((uint32_t) ((uint64_t) VBUS_MIN_MV * ADC_FULL * 100u / ((uint64_t) VDDA_MV * VBUS_DIVIDER_X100)))
#define ADC_START_MS 2u

static bool vbus_adc;  // ADC1 converts VBUS_SENSE

static bool wait_flag(uint32_t (*flag)(const ADC_TypeDef *)) {
  uint32_t t0 = HAL_GetTick();
  while (flag(ADC1) == 0u) {
    if (HAL_GetTick() - t0 > ADC_START_MS) {
      return false;
    }
  }
  return true;
}

static void vbus_init(void) {
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_2;
  gpio.Mode = GPIO_MODE_ANALOG;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOC, &gpio);

  HAL_PWREx_EnableVddA();
  __HAL_RCC_ADCDAC_CONFIG(RCC_ADCDACCLKSOURCE_HSE);  // 16 MHz
  __HAL_RCC_ADC12_CLK_ENABLE();

  LL_ADC_DisableDeepPowerDown(ADC1);
  LL_ADC_EnableInternalRegulator(ADC1);
  if (!wait_flag(LL_ADC_IsActiveFlag_LDORDY)) {
    return;
  }
  LL_ADC_REG_SetContinuousMode(ADC1, LL_ADC_REG_CONV_CONTINUOUS);
  LL_ADC_REG_SetOverrun(ADC1, LL_ADC_REG_OVR_DATA_OVERWRITTEN);
  LL_ADC_SetChannelPreselection(ADC1, LL_ADC_CHANNEL_3);
  LL_ADC_REG_SetSequencerRanks(ADC1, LL_ADC_REG_RANK_1, LL_ADC_CHANNEL_3);
  LL_ADC_SetChannelSamplingTime(ADC1, LL_ADC_CHANNEL_3, LL_ADC_SAMPLINGTIME_814CYCLES);
  LL_ADC_ClearFlag_ADRDY(ADC1);
  LL_ADC_Enable(ADC1);
  if (!wait_flag(LL_ADC_IsActiveFlag_ADRDY)) {
    return;
  }
  LL_ADC_REG_StartConversion(ADC1);
  vbus_adc = true;
}

void board_init(void) {
  family_core_init();
  HAL_Init();  // 1 ms SysTick, re-timed by the clock set-up
  clock_init();
  HAL_ICACHE_Enable();

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  GPIO_InitTypeDef gpio = {0};
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  for (int i = 0; i < LED_COUNT; i++) {
    gpio.Pin = leds[i].pin;
    HAL_GPIO_WritePin(leds[i].port, leds[i].pin, GPIO_PIN_RESET);
    HAL_GPIO_Init(leds[i].port, &gpio);
  }

  // User button B1: PC13, high when pressed (external pull-down on the board)
  gpio.Pin = GPIO_PIN_13;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLDOWN;
  HAL_GPIO_Init(GPIOC, &gpio);
  gpio.Pull = GPIO_NOPULL;

  // Log: USART1 TX on PA9 (RX on PA10 is unused), clocked from PCLK2
  gpio.Pin = GPIO_PIN_9 | GPIO_PIN_10;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Alternate = GPIO_AF7_USART1;
  HAL_GPIO_Init(GPIOA, &gpio);
  __HAL_RCC_USART1_CLK_ENABLE();
  family_log_uart_init(USART1, USART1_IRQn, HAL_RCC_GetPCLK2Freq(), LOG_BAUD);

  vbus_init();

  // USB: the pins; u2ps_dev_init() starts the HS PHY from the 16 MHz HSE
  // (U2PS_DWC2_HSE_HZ = HSE_VALUE)
  family_usb_pins();
}

void USART1_IRQHandler(void);  // vector table entry
void USART1_IRQHandler(void) {
  family_log_uart_irq();
}

void board_led(led_t led, bool on) {
  HAL_GPIO_WritePin(leds[led].port, leds[led].pin, on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

const char *board_led_name(led_t led) {
  return leds[led].name;
}

bool board_button(void) {
  return HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_13) == GPIO_PIN_SET;
}

// The last conversion; without the ADC, present (the device then shows a
// pulled cable as suspended, as without VBUS sensing)
bool board_vbus(void) {
  return !vbus_adc || LL_ADC_REG_ReadConversionData32(ADC1) >= VBUS_MIN_CODE;
}
