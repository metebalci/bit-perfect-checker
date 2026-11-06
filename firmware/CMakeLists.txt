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

# For a board with a status screen (app/screen.h), test builds only: a log
# line per completed redraw, and redrawing everything without a pause.
option(DISPLAY_LOG "Log each redraw of the status screen" OFF)
option(DISPLAY_STRESS "Redraw the whole status screen continuously" OFF)

# Test builds only, never a release: the console command "inject", which
# re-arms the audio OUT endpoint late on purpose (app/inject.h).
option(BPC_TEST_INJECT "Test builds only: the inject console command" OFF)

# The TinyUSB patches in lib/patches/ that the build applies: a list of
#   zlp      tinyusb-audio-out-zlp.patch (audio_device.c)
#   iso_out  tinyusb-dwc2-iso-out-next.patch (dcd_dwc2.c)
# or "none".
set(BPC_TUSB_PATCHES "zlp" CACHE STRING "TinyUSB patches: a list of zlp and iso_out, or none")

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

# TinyUSB device stack: core, audio class, Synopsys DWC2 controller. The
# patches named in BPC_TUSB_PATCHES go to copies in the build folder, so the
# submodule stays at the release; a file without a patch is compiled from the
# submodule. A patch that no longer applies exactly fails the build.
set(TUSB_PATCH_zlp ${TUSB}/class/audio/audio_device.c ${LIB}/patches/tinyusb-audio-out-zlp.patch)
set(TUSB_PATCH_iso_out ${TUSB}/portable/synopsys/dwc2/dcd_dwc2.c ${LIB}/patches/tinyusb-dwc2-iso-out-next.patch)
set(TUSB_PATCHES ${BPC_TUSB_PATCHES})
if(TUSB_PATCHES STREQUAL "none")
  set(TUSB_PATCHES "")
endif()
foreach(name IN LISTS TUSB_PATCHES)
  if(NOT DEFINED TUSB_PATCH_${name})
    message(FATAL_ERROR "BPC_TUSB_PATCHES: unknown patch '${name}' (zlp, iso_out or none)")
  endif()
endforeach()
set(TUSB_PATCHED_DIR ${CMAKE_BINARY_DIR}/tinyusb-patched)
find_program(PATCH patch REQUIRED)
foreach(name zlp iso_out)
  list(GET TUSB_PATCH_${name} 0 source)
  if(name IN_LIST TUSB_PATCHES)
    list(GET TUSB_PATCH_${name} 1 patch_file)
    get_filename_component(file ${source} NAME)
    set(patched ${TUSB_PATCHED_DIR}/${file})
    add_custom_command(
      OUTPUT ${patched}
      COMMAND ${CMAKE_COMMAND} -E remove -f ${patched}
      COMMAND ${CMAKE_COMMAND} -E copy ${source} ${patched}.tmp
      COMMAND ${PATCH} --quiet --forward --fuzz=0 --no-backup-if-mismatch --reject-file=-
              -i ${patch_file} ${patched}.tmp
      COMMAND ${CMAKE_COMMAND} -E rename ${patched}.tmp ${patched}
      DEPENDS ${source} ${patch_file}
      COMMENT "Patching TinyUSB's ${file}")
    set(TUSB_SOURCE_${name} ${patched})
  else()
    set(TUSB_SOURCE_${name} ${source})
  endif()
endforeach()
add_library(tinyusb STATIC
  ${TUSB}/tusb.c
  ${TUSB}/common/tusb_fifo.c
  ${TUSB}/device/usbd.c
  ${TUSB_SOURCE_zlp}
  ${TUSB_SOURCE_iso_out}
  ${TUSB}/portable/synopsys/dwc2/dwc2_common.c)
target_include_directories(tinyusb PUBLIC ${TUSB} ${APP})  # tusb_config.h
# The headers next to audio_device.c and dcd_dwc2.c, for their copies
target_include_directories(tinyusb PRIVATE ${TUSB}/class/audio ${TUSB}/portable/synopsys/dwc2)
target_compile_options(tinyusb PRIVATE ${WARNINGS})
target_link_libraries(tinyusb PUBLIC family_hal)

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
                           ALLOW_SILENCE_DEFAULT=$<IF:$<BOOL:${ALLOW_SILENCE}>,true,false>
                           $<$<BOOL:${DISPLAY_LOG}>:DISPLAY_LOG> $<$<BOOL:${DISPLAY_STRESS}>:DISPLAY_STRESS>)
target_compile_options(${FW} PRIVATE $<$<COMPILE_LANGUAGE:C>:${WARNINGS} -Werror>)
if(BPC_TEST_INJECT)
  message(STATUS "BPC_TEST_INJECT: a test build with the inject command, never a release")
  target_sources(${FW} PRIVATE ${APP}/inject.c)
  target_compile_definitions(${FW} PRIVATE BPC_TEST_INJECT)
  target_link_options(${FW} PRIVATE -Wl,--wrap=dcd_edpt_xfer)
endif()
target_link_libraries(${FW} PRIVATE tinyusb family_hal)
firmware_image(${FW} ${LINKER_SCRIPT})
