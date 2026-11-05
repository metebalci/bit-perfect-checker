# STM32F723E-DISCO

set(FAMILY stm32f7)
set(MCU_VARIANT stm32f723xx)  # CMSIS device header and startup file
set(HSE_VALUE 25000000)       # the USB HS PHY's reference: must be exact
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32f7/linker/stm32f723xe.ld)

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c)

# The board's 1.54" 240x240 display would add its sources here:
# list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c)
