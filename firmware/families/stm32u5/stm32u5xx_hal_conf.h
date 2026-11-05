// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32U5 HAL configuration: only the modules this firmware uses.

#ifndef STM32U5XX_HAL_CONF_H
#define STM32U5XX_HAL_CONF_H

#define HAL_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_ICACHE_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED

// Oscillators. HSE_VALUE comes from the board's board.cmake.
#ifndef HSE_VALUE
#error "the board must define HSE_VALUE"
#endif
#define HSE_STARTUP_TIMEOUT       100UL
#define MSI_VALUE                 4000000UL
#define HSI_VALUE                 16000000UL
#define HSI48_VALUE               48000000UL
#define LSI_VALUE                 32000UL
#define LSI_STARTUP_TIMEOUT       130UL
#define LSE_VALUE                 32768UL
#define LSE_STARTUP_TIMEOUT       5000UL
#define EXTERNAL_SAI1_CLOCK_VALUE 48000UL

#define VDD_VALUE                 3300UL
#define TICK_INT_PRIORITY         ((1UL << __NVIC_PRIO_BITS) - 1UL)
#define USE_RTOS                  0U
#define PREFETCH_ENABLE           1U

#define assert_param(expr) ((void) 0U)

#include "stm32u5xx_hal_rcc.h"
#include "stm32u5xx_hal_gpio.h"
#include "stm32u5xx_hal_icache.h"
#include "stm32u5xx_hal_cortex.h"
#include "stm32u5xx_hal_flash.h"
#include "stm32u5xx_hal_pwr.h"

#endif
