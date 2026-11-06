# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# The BPC1 generator: a USB host that is to play the BPC1 test signal to a
# UAC2 device. Its own CMake project next to the checker's, sharing
# cmake/common.cmake, the boards and the families.
#
#   cmake -S firmware/bpc1-generator -B firmware/build/bpc1-generator-<board> -G Ninja -DBOARD=<board>
#
# BOARD is a folder in boards/ whose board.cmake sets HOST_BOARD_SOURCES and
# HOST_LINKER_SCRIPT, and whose family sets FAMILY_HOST_SOURCES.

cmake_minimum_required(VERSION 3.21)

set(BOARD stm32f723e_disco CACHE STRING "Board: a folder in boards/ with a USB host port")
option(ENUM_CHECK "After each mount, set every rate and some formats of the device (a hardware check)" OFF)

set(CMAKE_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/../cmake/arm-none-eabi.cmake)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

project(bpc1_generator C ASM)

get_filename_component(FIRMWARE ${CMAKE_CURRENT_LIST_DIR}/.. REALPATH)
set(GEN ${CMAKE_CURRENT_LIST_DIR})

# Folders, compile options, warnings, the board and its family, the version
# stamp and firmware_image()
include(${FIRMWARE}/cmake/common.cmake)
if(NOT HOST_BOARD_SOURCES OR NOT HOST_LINKER_SCRIPT OR NOT FAMILY_HOST_SOURCES)
  message(FATAL_ERROR "Board '${BOARD}' has no USB host port: its board.cmake sets no HOST_BOARD_SOURCES "
                      "and HOST_LINKER_SCRIPT, or its family no FAMILY_HOST_SOURCES")
endif()

# TinyUSB host stack: core and the Synopsys DWC2 host controller; no hub and
# no class driver. Its tusb_config.h is this folder's. The host controller
# driver gets lib/patches/tinyusb-host-reserved-channels.patch, applied to a
# copy in the build folder as the checker's audio patch is, so the submodule
# stays at the release. A patch that no longer applies exactly fails the
# build.
set(DWC2 ${TUSB}/portable/synopsys/dwc2)
set(HCD_PATCH ${LIB}/patches/tinyusb-host-reserved-channels.patch)
set(HCD_PATCHED ${CMAKE_BINARY_DIR}/tinyusb-patched/hcd_dwc2.c)
find_program(PATCH patch REQUIRED)
add_custom_command(
  OUTPUT ${HCD_PATCHED}
  COMMAND ${CMAKE_COMMAND} -E remove -f ${HCD_PATCHED}
  COMMAND ${CMAKE_COMMAND} -E copy ${DWC2}/hcd_dwc2.c ${HCD_PATCHED}.tmp
  COMMAND ${PATCH} --quiet --forward --fuzz=0 --no-backup-if-mismatch --reject-file=-
          -i ${HCD_PATCH} ${HCD_PATCHED}.tmp
  COMMAND ${CMAKE_COMMAND} -E rename ${HCD_PATCHED}.tmp ${HCD_PATCHED}
  DEPENDS ${DWC2}/hcd_dwc2.c ${HCD_PATCH}
  COMMENT "Patching TinyUSB's hcd_dwc2.c")
add_library(tinyusb_host STATIC
  ${TUSB}/tusb.c
  ${TUSB}/common/tusb_fifo.c
  ${TUSB}/host/usbh.c
  ${HCD_PATCHED}
  ${DWC2}/dwc2_common.c)
target_include_directories(tinyusb_host PUBLIC ${GEN} ${TUSB})  # tusb_config.h
target_include_directories(tinyusb_host PRIVATE ${DWC2})  # headers next to hcd_dwc2.c
target_compile_options(tinyusb_host PRIVATE ${WARNINGS})
# With no built-in class driver, usbh.c compares a uint8_t with a count of 0
# (get_driver()), which -Wextra's -Wtype-limits reports
set_source_files_properties(${TUSB}/host/usbh.c PROPERTIES COMPILE_OPTIONS -Wno-type-limits)
target_link_libraries(tinyusb_host PUBLIC family_hal)

set(FW bpc1-generator-${BOARD})
add_executable(${FW}
  ${GEN}/main.c
  ${GEN}/console.c
  ${GEN}/vbus_switch.c
  ${GEN}/uac2_host.c
  ${GEN}/uac2_control.c
  ${APP}/display_none.c
  ${APP}/load.c
  ${APP}/log.c
  ${HOST_BOARD_SOURCES}
  ${BOARD_SOURCES}
  ${FAMILY_HOST_SOURCES}
  ${FAMILY_SOURCES})
# This folder first, so that its tusb_config.h is found before app/'s
target_include_directories(${FW} PRIVATE ${GEN} ${FIRMWARE}/include ${APP} ${CMAKE_BINARY_DIR})
target_compile_definitions(${FW} PRIVATE BOARD_NAME="${BOARD}" FAMILY_USB_HOST)
if(ENUM_CHECK)
  target_compile_definitions(${FW} PRIVATE GEN_ENUM_CHECK=1)
endif()
target_compile_options(${FW} PRIVATE $<$<COMPILE_LANGUAGE:C>:${WARNINGS} -Werror>)
target_link_libraries(${FW} PRIVATE tinyusb_host family_hal)
firmware_image(${FW} ${HOST_LINKER_SCRIPT})
