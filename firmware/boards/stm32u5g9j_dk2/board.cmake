# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32U5G9J-DK2

set(FAMILY stm32u5)
set(MCU_VARIANT stm32u5g9xx)  # CMSIS device header and startup file
set(HSE_VALUE 16000000)
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32u5/linker/stm32u5g9xj.ld)

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c ${CMAKE_CURRENT_LIST_DIR}/touch.c)

# The 5" 800x480 display (RK050HR18 on the LTDC, frame buffer in SRAM6) and
# its fonts from lib/spleen
list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c ${CMAKE_CURRENT_LIST_DIR}/panel_rk050hr18.c)
set(DISPLAY_FONTS 16x32=spleen-16x32.bdf 32x64=spleen-32x64.bdf)
