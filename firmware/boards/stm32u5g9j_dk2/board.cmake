# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32U5G9J-DK2

set(FAMILY stm32u5)
set(MCU_VARIANT stm32u5g9xx)  # CMSIS device header and startup file
set(HSE_VALUE 16000000)
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32u5/linker/stm32u5g9xj.ld)

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c ${CMAKE_CURRENT_LIST_DIR}/touch.c)

# The 5" 800x480 display (RK050HR18 on the LTDC, frame buffer in SRAM6) with
# the status screen, and its fonts from lib/spleen
list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c ${CMAKE_CURRENT_LIST_DIR}/panel_rk050hr18.c
     ${APP}/screen.c ${APP}/screen_model.c)
set(DISPLAY_FONTS 12x24=spleen-12x24.bdf 16x32=spleen-16x32.bdf 32x64=spleen-32x64.bdf)

# The panel's full green, 0-255: the RK050HR18's pure green looks lighter
# than the F723's panel's (clut_rgb332.h); 208 matches it by eye, 255 is no
# correction
set(PANEL_GREEN_MAX 208 CACHE STRING "STM32U5G9J-DK2: the panel's full green, 0-255")
add_compile_definitions(PANEL_GREEN_MAX=${PANEL_GREEN_MAX}u)
