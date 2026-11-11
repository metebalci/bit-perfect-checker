# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# bit-perfect-checker firmware.
#
#   cmake -S firmware -B firmware/build/<board> -G Ninja -DBOARD=<board> [-DSHIELD=<shield>]
#
# BOARD is a folder in boards/, SHIELD an optional folder in shields/.

cmake_minimum_required(VERSION 3.22)

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

# For a board with a status screen (app/screen.h), test builds only: a log
# line per completed redraw, and redrawing everything without a pause.
option(DISPLAY_LOG "Log each redraw of the status screen" OFF)
option(DISPLAY_STRESS "Redraw the whole status screen continuously" OFF)

# Test builds only, never a release: the console command "inject", which
# re-arms the audio OUT endpoint late on purpose (app/inject.h), on u2ps's
# injection hook (U2PS_TEST_INJECT).
option(BPC_TEST_INJECT "Test builds only: the inject console command" OFF)

# The u2ps source tree (uac2-playback-stack), the checker's USB device stack,
# used unmodified: by default the submodule lib/u2ps, pinned to a u2ps release.
set(U2PS_DIR ${CMAKE_CURRENT_LIST_DIR}/lib/u2ps CACHE PATH "The u2ps source tree")

# Link-time optimisation of u2ps's libraries (u2ps's option U2PS_LTO), on for
# release builds. A test build that wraps a u2ps function with -Wl,--wrap
# (the main loop's masked time, u2ps_dport_irq_mask()) needs it off: with LTO
# the wrap does not reach calls inside u2ps.
set(U2PS_LTO ON CACHE BOOL "Link-time optimisation of u2ps's libraries (release builds: ON)")

set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/cmake/arm-none-eabi.cmake)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

project(bit_perfect_checker C ASM)

set(FIRMWARE ${CMAKE_CURRENT_LIST_DIR})

# Folders, compile options, warnings, the board and its family, the version
# stamp and firmware_image()
include(${FIRMWARE}/cmake/common.cmake)

# Then the shield
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

# u2ps device stack for the board's chip (U2PS_CHIP, board.cmake) with the
# PHY's reference at the board's HSE: VBUS read by the app (vbus.c), and the
# control hook for the log's "usb unsupported" and SET_INTERFACE lines. Its
# build constants are normal variables here, so the board and its family
# decide them and not the cache: U2PS_ISO_OUT_COUNT_MAX, the most buffers of
# a stream, comes from the family (family.cmake). A -D of one of these would
# change nothing, so it stops the configure; the cache holds each one's value
# in use, as an internal entry. u2ps's libraries take this project's
# CMAKE_BUILD_TYPE (set above, before project()).
if(NOT EXISTS ${U2PS_DIR}/device/u2ps_dev.h)
  message(FATAL_ERROR "U2PS_DIR must name the u2ps source tree (with device/u2ps_dev.h), not '${U2PS_DIR}'")
endif()
if(NOT U2PS_CHIP)
  message(FATAL_ERROR "Board ${BOARD} names no U2PS_CHIP in its board.cmake")
endif()
set(U2PS_DEVICE ON)
set(U2PS_VBUS app)
set(U2PS_CONTROL_HOOK ON)
set(U2PS_DWC2_HSE_HZ ${HSE_VALUE})
set(U2PS_TEST_INJECT ${BPC_TEST_INJECT})
set(U2PS_DECIDED U2PS_CHIP U2PS_DEVICE U2PS_VBUS U2PS_CONTROL_HOOK U2PS_DWC2_HSE_HZ U2PS_TEST_INJECT
    U2PS_ISO_OUT_COUNT_MAX)
foreach(v IN LISTS U2PS_DECIDED)
  # Each run leaves the value in use as an internal entry (below); any other
  # entry, or another value, came from a -D
  get_property(type CACHE ${v} PROPERTY TYPE)
  if(DEFINED CACHE{${v}} AND (NOT type STREQUAL "INTERNAL" OR NOT "$CACHE{${v}}" STREQUAL "$CACHE{BPC_IN_USE_${v}}"))
    message(FATAL_ERROR "${v} is this project's (${${v}} for ${BOARD}), not a cache option: configure without "
            "-D${v}, and remove it from the cache with -U${v}")
  endif()
endforeach()
add_subdirectory(${U2PS_DIR} ${CMAKE_BINARY_DIR}/u2ps EXCLUDE_FROM_ALL)
foreach(v IN LISTS U2PS_DECIDED)
  set(${v} "${${v}}" CACHE INTERNAL "u2ps build constant, set by this project")
  set(BPC_IN_USE_${v} "${${v}}" CACHE INTERNAL "")
endforeach()
message(STATUS "USB stack: u2ps from ${U2PS_DIR}, LTO ${U2PS_LTO}")

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
  ${APP}/u2ps_glue.c
  ${APP}/usb_formats.c
  ${APP}/vbus.c
  ${FIRMWARE}/bpc1/bpc1.c
  ${FIRMWARE}/bpc1/bpc1_checker.c
  ${DISPLAY_SOURCES}
  ${BOARD_SOURCES}
  ${FAMILY_SOURCES})
target_include_directories(${FW} PRIVATE ${FIRMWARE}/include ${APP} ${FIRMWARE}/bpc1 ${CMAKE_BINARY_DIR})
if(DISPLAY_FONTS)
  target_include_directories(${FW} PRIVATE ${FONTS_DIR})
endif()
target_compile_definitions(${FW} PRIVATE BOARD_NAME="${BOARD}" USB_VID=${USB_VID} USB_PID=${USB_PID}
                           USB_SERIAL_PREFIX="${USB_SERIAL_PREFIX}"
                           UAC2_FORMATS_DEFAULT="${UAC2_FORMATS}"
                           CLASS_MEM_BYTES=${FAMILY_CLASS_MEM_BYTES}u CLASS_MEM_BUFFERS_MIN=40u
                           ALLOW_SILENCE_DEFAULT=$<IF:$<BOOL:${ALLOW_SILENCE}>,true,false>
                           $<$<BOOL:${DISPLAY_LOG}>:DISPLAY_LOG> $<$<BOOL:${DISPLAY_STRESS}>:DISPLAY_STRESS>)
target_compile_options(${FW} PRIVATE $<$<COMPILE_LANGUAGE:C>:${WARNINGS} -Werror>)
if(BPC_TEST_INJECT)
  message(STATUS "BPC_TEST_INJECT: a test build with the inject command, never a release")
  target_sources(${FW} PRIVATE ${APP}/inject.c)
  target_compile_definitions(${FW} PRIVATE BPC_TEST_INJECT)
endif()
target_link_libraries(${FW} PRIVATE u2ps_device family_hal)
firmware_image(${FW} ${LINKER_SCRIPT})
