# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# bit-perfect-checker firmware.
#
#   cmake -S firmware -B firmware/build/<board> -G Ninja -DBOARD=<board> [-DSHIELD=<shield>]
#
# BOARD is a folder in boards/, SHIELD an optional folder in shields/.

cmake_minimum_required(VERSION 3.21)

set(BOARD nucleo_u5a5zj_q CACHE STRING "Board: a folder in boards/")
set(SHIELD "" CACHE STRING "Optional shield: a folder in shields/")

# Objective Development's shared VID:PID for audio class devices (V-USB's
# USB-IDs-for-free.txt). Its rules: the serial number string starts with an
# Internet domain or e-mail address its maker owns, ended by a colon, and host
# software matches the serial number too. The firmware's serial number is
# USB_SERIAL_PREFIX followed by the chip's unique ID in hex. Whoever
# distributes a modified firmware sets USB_SERIAL_PREFIX to a domain or e-mail
# address of their own.
set(USB_VID 0x16C0 CACHE STRING "USB vendor ID")
set(USB_PID 0x27E0 CACHE STRING "USB product ID")
set(USB_SERIAL_PREFIX "bpc.metebalci.com:" CACHE STRING
    "Start of the USB serial number: a domain or e-mail address and a colon")

# The formats the device offers at boot, as alt settings 1 to n in this
# order, each <bits>/<bytes>; the console command "formats" changes them.
set(UAC2_FORMATS "16/2,24/4,32/4,24/3" CACHE STRING "Formats offered at boot")

# Silence outside every file gives no result, from boot on; the console
# command "allow_silence" changes it.
option(ALLOW_SILENCE "Silence outside every file gives no result" ON)

set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi.cmake)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

project(bit_perfect_checker C ASM)

set(FIRMWARE ${CMAKE_CURRENT_LIST_DIR})
get_filename_component(REPO_ROOT ${FIRMWARE}/.. REALPATH)
set(LIB ${FIRMWARE}/lib)
set(APP ${FIRMWARE}/app)

# Before the family, so that its HAL library gets these too
add_compile_options(-ffunction-sections -fdata-sections -fno-common -ffile-prefix-map=${REPO_ROOT}/=)

# Board, then its family, then the shield
if(NOT EXISTS ${FIRMWARE}/boards/${BOARD}/board.cmake)
  message(FATAL_ERROR "Unknown BOARD '${BOARD}': no boards/${BOARD}/board.cmake")
endif()
set(DISPLAY_SOURCES "")
include(${FIRMWARE}/boards/${BOARD}/board.cmake)
include(${FIRMWARE}/families/${FAMILY}/family.cmake)
if(SHIELD)
  if(NOT EXISTS ${FIRMWARE}/shields/${SHIELD}/shield.cmake)
    message(FATAL_ERROR "Unknown SHIELD '${SHIELD}': no shields/${SHIELD}/shield.cmake")
  endif()
  include(${FIRMWARE}/shields/${SHIELD}/shield.cmake)
endif()
if(NOT DISPLAY_SOURCES)
  set(DISPLAY_SOURCES ${APP}/display_none.c)
endif()

# A display's fonts: a board or shield lists them in DISPLAY_FONTS as
# <name>=<file>, <file> a BDF font in lib/spleen, and gets font_<name> from
# fonts.h. tools/fontgen.py writes fonts.c and fonts.h into the build folder,
# so only display builds need Python.
if(DISPLAY_FONTS)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  set(FONTS_DIR ${CMAKE_BINARY_DIR}/fonts)
  set(fontgen_args "")
  set(fontgen_bdf "")
  foreach(font IN LISTS DISPLAY_FONTS)
    string(REPLACE "=" ";" font_parts "${font}")
    list(GET font_parts 0 font_name)
    list(GET font_parts 1 font_file)
    list(APPEND fontgen_args "${font_name}=${LIB}/spleen/${font_file}")
    list(APPEND fontgen_bdf ${LIB}/spleen/${font_file})
  endforeach()
  add_custom_command(
    OUTPUT ${FONTS_DIR}/fonts.c ${FONTS_DIR}/fonts.h
    COMMAND Python3::Interpreter -I ${FIRMWARE}/tools/fontgen.py --out ${FONTS_DIR} ${fontgen_args}
    DEPENDS ${FIRMWARE}/tools/fontgen.py ${fontgen_bdf}
    COMMENT "Generating the display fonts")
  list(APPEND DISPLAY_SOURCES ${FONTS_DIR}/fonts.c ${FONTS_DIR}/fonts.h)
endif()

# The warning set TinyUSB builds its examples with
set(WARNINGS
  -Wall -Wextra -Wdouble-promotion -Wstrict-prototypes -Wstrict-overflow
  -Werror-implicit-function-declaration -Wfloat-equal -Wundef -Wshadow -Wwrite-strings
  -Wsign-compare -Wmissing-format-attribute -Wunreachable-code -Wcast-align
  -Wcast-function-type -Wcast-qual -Wnull-dereference -Wuninitialized -Wunused
  -Wreturn-type -Wredundant-decls -Wmissing-prototypes)

# TinyUSB device stack: core, audio class, Synopsys DWC2 controller. The
# audio class driver gets the fix in lib/patches/, applied to a copy in the
# build folder, so the submodule stays at the release. A patch that no longer
# applies exactly fails the build.
set(TUSB ${LIB}/tinyusb/src)
set(AUDIO ${TUSB}/class/audio)
set(AUDIO_PATCH ${LIB}/patches/tinyusb-audio-out-zlp.patch)
set(AUDIO_PATCHED_DIR ${CMAKE_BINARY_DIR}/tinyusb-patched)
set(AUDIO_PATCHED ${AUDIO_PATCHED_DIR}/audio_device.c)
find_program(PATCH patch REQUIRED)
add_custom_command(
  OUTPUT ${AUDIO_PATCHED}
  COMMAND ${CMAKE_COMMAND} -E remove -f ${AUDIO_PATCHED}
  COMMAND ${CMAKE_COMMAND} -E copy ${AUDIO}/audio_device.c ${AUDIO_PATCHED}.tmp
  COMMAND ${PATCH} --quiet --forward --fuzz=0 --no-backup-if-mismatch --reject-file=-
          -i ${AUDIO_PATCH} ${AUDIO_PATCHED}.tmp
  COMMAND ${CMAKE_COMMAND} -E rename ${AUDIO_PATCHED}.tmp ${AUDIO_PATCHED}
  DEPENDS ${AUDIO}/audio_device.c ${AUDIO_PATCH}
  COMMENT "Patching TinyUSB's audio_device.c")
add_library(tinyusb STATIC
  ${TUSB}/tusb.c
  ${TUSB}/common/tusb_fifo.c
  ${TUSB}/device/usbd.c
  ${AUDIO_PATCHED}
  ${TUSB}/portable/synopsys/dwc2/dcd_dwc2.c
  ${TUSB}/portable/synopsys/dwc2/dwc2_common.c)
target_include_directories(tinyusb PUBLIC ${TUSB} ${APP})  # tusb_config.h
target_include_directories(tinyusb PRIVATE ${AUDIO})  # headers next to audio_device.c
target_compile_options(tinyusb PRIVATE ${WARNINGS})
target_link_libraries(tinyusb PUBLIC family_hal)

# Version stamp, regenerated at every build
set(VERSION_H ${CMAKE_BINARY_DIR}/version.h)
add_custom_target(version
  COMMAND ${CMAKE_COMMAND} -DSRC=${REPO_ROOT} -DOUT=${VERSION_H} -P ${FIRMWARE}/cmake/version.cmake
  BYPRODUCTS ${VERSION_H})

set(FW bpc-${BOARD})
if(SHIELD)
  set(FW ${FW}-${SHIELD})
endif()
add_executable(${FW}
  ${APP}/main.c
  ${APP}/console.c
  ${APP}/leds.c
  ${APP}/load.c
  ${APP}/log.c
  ${APP}/stream.c
  ${APP}/uac2.c
  ${APP}/usb_descriptors.c
  ${FIRMWARE}/bpc1/bpc1.c
  ${FIRMWARE}/bpc1/bpc1_checker.c
  ${DISPLAY_SOURCES}
  ${BOARD_SOURCES}
  ${FAMILY_SOURCES})
set_target_properties(${FW} PROPERTIES SUFFIX .elf)
add_dependencies(${FW} version)
target_include_directories(${FW} PRIVATE ${FIRMWARE}/include ${APP} ${FIRMWARE}/bpc1 ${CMAKE_BINARY_DIR})
if(DISPLAY_FONTS)
  target_include_directories(${FW} PRIVATE ${FONTS_DIR})
endif()
target_compile_definitions(${FW} PRIVATE BOARD_NAME="${BOARD}" USB_VID=${USB_VID} USB_PID=${USB_PID}
                           USB_SERIAL_PREFIX="${USB_SERIAL_PREFIX}"
                           UAC2_FORMATS_DEFAULT="${UAC2_FORMATS}"
                           ALLOW_SILENCE_DEFAULT=$<IF:$<BOOL:${ALLOW_SILENCE}>,true,false>)
target_compile_options(${FW} PRIVATE $<$<COMPILE_LANGUAGE:C>:${WARNINGS} -Werror>)
target_link_libraries(${FW} PRIVATE tinyusb family_hal)
target_link_options(${FW} PRIVATE
  ${CPU_FLAGS}
  -T${LINKER_SCRIPT}
  -nostartfiles --specs=nano.specs
  -Wl,--gc-sections -Wl,-Map=${FW}.map -Wl,--print-memory-usage)
set_property(TARGET ${FW} APPEND PROPERTY LINK_DEPENDS ${LINKER_SCRIPT})

add_custom_command(TARGET ${FW} POST_BUILD
  COMMAND ${CMAKE_OBJCOPY} -O binary ${FW}.elf ${FW}.bin
  COMMAND ${CMAKE_OBJCOPY} -O ihex ${FW}.elf ${FW}.hex
  COMMAND ${CMAKE_SIZE} ${FW}.elf
  BYPRODUCTS ${FW}.bin ${FW}.hex)

family_flash_target(${FW} ${FW}.bin)
