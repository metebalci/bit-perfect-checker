# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32U5G9J-DK2

set(FAMILY stm32u5)
set(MCU_VARIANT stm32u5g9xx)  # CMSIS device header and startup file
set(HSE_VALUE 16000000)
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32u5/linker/stm32u5g9xj.ld)

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c)
