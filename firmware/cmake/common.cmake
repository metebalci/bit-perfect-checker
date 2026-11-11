# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# For the checker (firmware/CMakeLists.txt): folders, compile options, the
# warning set, the board and family, the version stamp and the image.
# Included after project(), with FIRMWARE set to the firmware folder and
# BOARD to a folder in boards/.

get_filename_component(REPO_ROOT ${FIRMWARE}/.. REALPATH)
set(LIB ${FIRMWARE}/lib)
set(APP ${FIRMWARE}/app)

# Before the family, so that its HAL library gets these too
add_compile_options(-ffunction-sections -fdata-sections -fno-common -ffile-prefix-map=${REPO_ROOT}/=)

# Board, then its family
if(NOT EXISTS ${FIRMWARE}/boards/${BOARD}/board.cmake)
  message(FATAL_ERROR "Unknown BOARD '${BOARD}': no boards/${BOARD}/board.cmake")
endif()
set(DISPLAY_SOURCES "")
include(${FIRMWARE}/boards/${BOARD}/board.cmake)
include(${FIRMWARE}/families/${FAMILY}/family.cmake)

# The warning set TinyUSB builds its examples with
set(WARNINGS
  -Wall -Wextra -Wdouble-promotion -Wstrict-prototypes -Wstrict-overflow
  -Werror-implicit-function-declaration -Wfloat-equal -Wundef -Wshadow -Wwrite-strings
  -Wsign-compare -Wmissing-format-attribute -Wunreachable-code -Wcast-align
  -Wcast-function-type -Wcast-qual -Wnull-dereference -Wuninitialized -Wunused
  -Wreturn-type -Wredundant-decls -Wmissing-prototypes)

# Version stamp, written at configure time (a firmware tag that is not
# firmware/VERSION stops the configure) and again at every build
set(VERSION_H ${CMAKE_BINARY_DIR}/version.h)
execute_process(
  COMMAND ${CMAKE_COMMAND} -DSRC=${REPO_ROOT} -DOUT=${VERSION_H} -P ${FIRMWARE}/cmake/version.cmake
  RESULT_VARIABLE version_result)
if(NOT version_result EQUAL 0)
  message(FATAL_ERROR "No firmware version (cmake/version.cmake)")
endif()
add_custom_target(version
  COMMAND ${CMAKE_COMMAND} -DSRC=${REPO_ROOT} -DOUT=${VERSION_H} -P ${FIRMWARE}/cmake/version.cmake
  BYPRODUCTS ${VERSION_H})

# The executable <target> as an image: linked with <linker script>, then
# <target>.bin and .hex next to the .elf, and the "flash" target
function(firmware_image target linker_script)
  set_target_properties(${target} PROPERTIES SUFFIX .elf)
  add_dependencies(${target} version)
  target_link_options(${target} PRIVATE
    ${CPU_FLAGS}
    -T${linker_script}
    -nostartfiles --specs=nano.specs
    -Wl,--gc-sections -Wl,-Map=${target}.map -Wl,--print-memory-usage)
  set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS ${linker_script})

  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary ${target}.elf ${target}.bin
    COMMAND ${CMAKE_OBJCOPY} -O ihex ${target}.elf ${target}.hex
    COMMAND ${CMAKE_SIZE} ${target}.elf
    BYPRODUCTS ${target}.bin ${target}.hex)

  family_flash_target(${target} ${target}.bin)
endfunction()
