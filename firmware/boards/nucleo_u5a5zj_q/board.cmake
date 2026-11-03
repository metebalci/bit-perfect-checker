# NUCLEO-U5A5ZJ-Q

set(FAMILY stm32u5)
set(MCU_VARIANT stm32u5a5xx)  # CMSIS device header and startup file
set(HSE_VALUE 16000000)
set(LINKER_SCRIPT ${FIRMWARE}/families/stm32u5/linker/stm32u5a5xj.ld)

set(BOARD_SOURCES ${CMAKE_CURRENT_LIST_DIR}/board.c)

# A display, if the board had one, would add its sources here:
# list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c)
