# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32F7 family: CPU flags, ST's CMSIS device files and HAL, the startup
# file and the family glue. The board sets MCU_VARIANT (e.g. stm32f723xx) and
# HSE_VALUE before this is included.

set(HAL ${LIB}/stm32f7xx_hal_driver)
set(CMSIS_DEVICE ${LIB}/cmsis_device_f7)
set(CMSIS_CORE ${LIB}/CMSIS_5/CMSIS/Core/Include)

# The F72x/F73x Cortex-M7 has a single-precision FPU
set(CPU_FLAGS -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-sp-d16)
string(TOUPPER ${MCU_VARIANT} MCU_DEFINE)
string(REPLACE "XX" "xx" MCU_DEFINE ${MCU_DEFINE})  # stm32f723xx -> STM32F723xx

add_compile_options(${CPU_FLAGS})
# TinyUSB port 0 is OTG FS and port 1 OTG HS on the STM32F7; the device
# (the checker) and the host (the BPC1 generator) run on OTG HS
add_compile_definitions(${MCU_DEFINE} USE_HAL_DRIVER HSE_VALUE=${HSE_VALUE}UL CFG_TUSB_MCU=OPT_MCU_STM32F7
                        BOARD_TUD_RHPORT=1 BOARD_TUH_RHPORT=1)
# TinyUSB's USB buffers (CFG_TUD_MEM_SECTION, CFG_TUH_MEM_SECTION) in DTCM,
# where the CPU reads them at zero wait states and the OTG HS DMA reaches
# them through the core's AHBS port; the linker script places .dtcm_usb.
add_compile_definitions("CFG_TUD_MEM_SECTION=__attribute__((section(\".dtcm_usb\")))"
                        "CFG_TUH_MEM_SECTION=__attribute__((section(\".dtcm_usb\")))")

# The HAL modules in use
add_library(family_hal STATIC
  ${CMSIS_DEVICE}/Source/Templates/system_stm32f7xx.c
  ${HAL}/Src/stm32f7xx_hal.c
  ${HAL}/Src/stm32f7xx_hal_cortex.c
  ${HAL}/Src/stm32f7xx_hal_gpio.c
  ${HAL}/Src/stm32f7xx_hal_pwr.c
  ${HAL}/Src/stm32f7xx_hal_pwr_ex.c
  ${HAL}/Src/stm32f7xx_hal_rcc.c
  ${HAL}/Src/stm32f7xx_hal_rcc_ex.c)
# Vendor headers as system headers, so that their warnings stay out of ours
target_include_directories(family_hal SYSTEM PUBLIC ${CMSIS_CORE} ${CMSIS_DEVICE}/Include ${HAL}/Inc)
target_include_directories(family_hal PUBLIC ${CMAKE_CURRENT_LIST_DIR})  # stm32f7xx_hal_conf.h, family.h

set(FAMILY_SOURCES
  ${CMAKE_CURRENT_LIST_DIR}/family.c
  ${CMSIS_DEVICE}/Source/Templates/gcc/startup_${MCU_VARIANT}.s)
# The BPC1 generator's USB host part, which replaces the USB device part of
# family.c (FAMILY_USB_HOST)
set(FAMILY_HOST_SOURCES ${CMAKE_CURRENT_LIST_DIR}/family_usb_host.c)

include(${FIRMWARE}/cmake/stm32cubeprog.cmake)
