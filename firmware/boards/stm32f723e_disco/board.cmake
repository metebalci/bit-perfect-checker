# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# STM32F723E-DISCO

set(FAMILY stm32f7)
set(MCU_VARIANT stm32f723xx)  # CMSIS device header and startup file
set(HSE_VALUE 25000000)       # the USB HS PHY's reference: must be exact
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32f7/linker/stm32f723xe.ld)
set(U2PS_CHIP stm32f723)      # the checker's USB stack, u2ps

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c ${CMAKE_CURRENT_LIST_DIR}/touch.c)

# The 1.54" 240x240 display (ST7789H2 on the FMC) with the status screen,
# and its fonts from lib/spleen
list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c ${CMAKE_CURRENT_LIST_DIR}/panel_st7789h2.c
     ${APP}/screen.c ${APP}/screen_model.c)
set(DISPLAY_FONTS 8x16=spleen-8x16.bdf 12x24=spleen-12x24.bdf 16x32=spleen-16x32.bdf)
