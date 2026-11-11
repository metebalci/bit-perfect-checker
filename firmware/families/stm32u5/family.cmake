# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32U5 family: CPU flags, ST's CMSIS device files and HAL, the startup
# file and the family glue. The board sets MCU_VARIANT (e.g. stm32u5a5xx) and
# HSE_VALUE before this is included.

set(HAL ${LIB}/stm32u5xx_hal_driver)
set(CMSIS_DEVICE ${LIB}/cmsis_device_u5)
set(CMSIS_CORE ${LIB}/CMSIS_5/CMSIS/Core/Include)

set(CPU_FLAGS -mcpu=cortex-m33 -mthumb -mfloat-abi=hard -mfpu=fpv5-sp-d16)
string(TOUPPER ${MCU_VARIANT} MCU_DEFINE)
string(REPLACE "XX" "xx" MCU_DEFINE ${MCU_DEFINE})  # stm32u5a5xx -> STM32U5A5xx

add_compile_options(${CPU_FLAGS})
add_compile_definitions(${MCU_DEFINE} USE_HAL_DRIVER HSE_VALUE=${HSE_VALUE}UL)

# The checker's class memory, u2ps's buffers for a stream (app/u2ps_glue.c):
# 1 MB of the 2496 KB of SRAM, 1351 buffers (169 ms) at 32/4 (776-byte
# stride), 2702 (338 ms) at 16/2 (388). U2PS_ISO_OUT_COUNT_MAX, the most
# buffers of a stream, is the class memory at the smallest stride, the 16/2
# format's (u2ps_glue.c checks it).
set(FAMILY_CLASS_MEM_BYTES 1048576)
set(U2PS_ISO_OUT_COUNT_MAX 2702)

# The HAL modules in use
add_library(family_hal STATIC
  ${CMSIS_DEVICE}/Source/Templates/system_stm32u5xx.c
  ${HAL}/Src/stm32u5xx_hal.c
  ${HAL}/Src/stm32u5xx_hal_cortex.c
  ${HAL}/Src/stm32u5xx_hal_gpio.c
  ${HAL}/Src/stm32u5xx_hal_icache.c
  ${HAL}/Src/stm32u5xx_hal_pwr.c
  ${HAL}/Src/stm32u5xx_hal_pwr_ex.c
  ${HAL}/Src/stm32u5xx_hal_rcc.c
  ${HAL}/Src/stm32u5xx_hal_rcc_ex.c)
# Vendor headers as system headers, so that their warnings stay out of ours
target_include_directories(family_hal SYSTEM PUBLIC ${CMSIS_CORE} ${CMSIS_DEVICE}/Include ${HAL}/Inc)
target_include_directories(family_hal PUBLIC ${CMAKE_CURRENT_LIST_DIR})  # stm32u5xx_hal_conf.h, family.h

set(FAMILY_SOURCES
  ${CMAKE_CURRENT_LIST_DIR}/family.c
  ${CMSIS_DEVICE}/Source/Templates/gcc/startup_${MCU_VARIANT}.s)

include(${FIRMWARE}/cmake/stm32cubeprog.cmake)
