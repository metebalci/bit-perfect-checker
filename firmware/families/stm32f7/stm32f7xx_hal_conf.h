// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// STM32F7 HAL configuration: only the modules this firmware uses.

#ifndef STM32F7XX_HAL_CONF_H
#define STM32F7XX_HAL_CONF_H

#define HAL_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED

// Oscillators. HSE_VALUE comes from the board's board.cmake.
#ifndef HSE_VALUE
#error "the board must define HSE_VALUE"
#endif
#define HSE_STARTUP_TIMEOUT       100U
#define HSI_VALUE                 16000000U
#define LSI_VALUE                 32000U
#define LSE_VALUE                 32768U
#define LSE_STARTUP_TIMEOUT       5000U
#define EXTERNAL_CLOCK_VALUE      12288000U

#define VDD_VALUE                 3300U
#define TICK_INT_PRIORITY         ((1UL << __NVIC_PRIO_BITS) - 1UL)
#define USE_RTOS                  0U
#define PREFETCH_ENABLE           1U
#define ART_ACCELERATOR_ENABLE    1U

#define assert_param(expr) ((void) 0U)

#include "stm32f7xx_hal_rcc.h"
#include "stm32f7xx_hal_gpio.h"
#include "stm32f7xx_hal_cortex.h"
#include "stm32f7xx_hal_flash.h"
#include "stm32f7xx_hal_pwr.h"

#endif
