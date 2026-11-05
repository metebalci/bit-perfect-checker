// STM32U5G9J-DK2: STM32U5G9ZJT6Q (SMPS), 16 MHz HSE crystal, LD2 red PD2 and
// LD3 green PD4, both active low, no blue LED, user button B2 on PC13 (high
// when pressed; B1 is reset), STLINK-V3EC virtual COM port on USART1
// PA9/PA10, user USB-C (CN2) on the OTG HS PHY. Clocks and USB as on the
// NUCLEO-U5A5ZJ-Q, which ST's STM32CubeU5 examples for this board match.

#include "board.h"

#include <stddef.h>

#include "family.h"
#include "log.h"

typedef struct {
  GPIO_TypeDef *port;
  uint16_t pin;
  const char *name;
} led_pin_t;

// Both LEDs sit between 3V3 and the pin: a low pin lights them. LED_BLUE
// has no port, so board_led() ignores it and the LED test skips it.
static const led_pin_t leds[LED_COUNT] = {
  [LED_GREEN] = {GPIOD, GPIO_PIN_4, "green PD4"},
  [LED_BLUE]  = {NULL, 0, NULL},
  [LED_RED]   = {GPIOD, GPIO_PIN_2, "red PD2"},
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

  // VDDIO2 is tied to VDD on this board and no port G pin is used, so it
  // stays as it is.
  if (HAL_PWREx_ConfigSupply(PWR_SMPS_SUPPLY) != HAL_OK) {
    halt();
  }
}

void board_init(void) {
  family_core_init();
  HAL_Init();  // 1 ms SysTick, re-timed by the clock set-up
  clock_init();
  HAL_ICACHE_Enable();

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  GPIO_InitTypeDef gpio = {0};
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  for (int i = 0; i < LED_COUNT; i++) {
    if (leds[i].port == NULL) {
      continue;
    }
    gpio.Pin = leds[i].pin;
    HAL_GPIO_WritePin(leds[i].port, leds[i].pin, GPIO_PIN_SET);  // off
    HAL_GPIO_Init(leds[i].port, &gpio);
  }

  // User button B2: PC13, high when pressed; no pull-down on the board
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

  // USB HS PHY clocked from the 16 MHz HSE
  family_usb_init(RCC_USBPHYCLKSOURCE_HSE, SYSCFG_OTG_HS_PHY_CLK_SELECT_1);
}

void USART1_IRQHandler(void);  // vector table entry
void USART1_IRQHandler(void) {
  family_log_uart_irq();
}

void board_led(led_t led, bool on) {
  if (leds[led].port == NULL) {
    return;
  }
  HAL_GPIO_WritePin(leds[led].port, leds[led].pin, on ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

const char *board_led_name(led_t led) {
  return leds[led].name;
}

bool board_button(void) {
  return HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_13) == GPIO_PIN_SET;
}
