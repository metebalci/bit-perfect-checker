# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# Host tools for the tests in tests/ that run the checker's USB code against
# u2ps's PC test kit (u2ps AGENTS.md section 7): the u2ps device stack with a
# fake port, built with the checker's options, and the kit playing the host
# (tests/host_tools.py builds them). Not part of the firmware.
#
#   cmake -S firmware/app/host -B <build folder> [-DU2PS_DIR=<u2ps tree>]

cmake_minimum_required(VERSION 3.22)
set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type")
project(bpc_host_tools C)

set(U2PS_DIR ${CMAKE_CURRENT_LIST_DIR}/../../lib/u2ps CACHE PATH "The u2ps source tree (default: the submodule)")
if(NOT EXISTS ${U2PS_DIR}/device/u2ps_dev.h)
  message(FATAL_ERROR "U2PS_DIR must name the u2ps source tree (with device/u2ps_dev.h), not '${U2PS_DIR}'")
endif()

set(APP ${CMAKE_CURRENT_LIST_DIR}/..)
set(FIRMWARE ${APP}/..)

# u2ps as the firmware builds it (firmware/CMakeLists.txt), for streams of
# up to 2048 buffers. The tools lend less class memory, so that a test holds
# every buffer with a few packets: 10 buffers at 32/4 and 20 at 16/2; the
# _big tools 2048 at 16/2, as many as a U5 board lends at 32/4 and more.
set(CLASS_MEM_BYTES 7760)
set(CLASS_MEM_BYTES_BIG 794624)
set(CLASS_MEM_BUFFERS_MIN 9)
set(U2PS_DEVICE ON)
set(U2PS_VBUS app)
set(U2PS_CONTROL_HOOK ON)
set(U2PS_ISO_OUT_COUNT_MAX 2048)
add_subdirectory(${U2PS_DIR} u2ps EXCLUDE_FROM_ALL)

set(HOST_WARNINGS -Wall -Wextra -Werror -Wshadow -Wundef -Wstrict-prototypes -Wmissing-prototypes -Wcast-qual
    -Wredundant-decls)

# One tool per test, each with the kit's device library, which brings the
# stack, the fake port and the sanitizers
function(host_tool name class_mem)
  add_executable(${name} ${ARGN})
  target_include_directories(${name} PRIVATE ${APP}/host/stub ${APP} ${FIRMWARE}/include ${FIRMWARE}/bpc1)
  target_compile_definitions(${name} PRIVATE CLASS_MEM_BYTES=${class_mem}u
                             CLASS_MEM_BUFFERS_MIN=${CLASS_MEM_BUFFERS_MIN}u
                             UAC2_FORMATS_DEFAULT="16/2,24/4,32/4,24/3")
  target_compile_options(${name} PRIVATE ${HOST_WARNINGS})
  target_link_libraries(${name} PRIVATE u2ps_testkit_device)
endfunction()

set(STREAM_SOURCES stream_tool.c ${APP}/stream.c ${APP}/u2ps_glue.c ${APP}/usb_formats.c
    ${FIRMWARE}/bpc1/bpc1.c ${FIRMWARE}/bpc1/bpc1_checker.c)
host_tool(usb_desc_tool ${CLASS_MEM_BYTES} usb_desc_tool.c ${APP}/usb_formats.c)
host_tool(stream_tool ${CLASS_MEM_BYTES} ${STREAM_SOURCES})
host_tool(stream_tool_big ${CLASS_MEM_BYTES_BIG} ${STREAM_SOURCES})
