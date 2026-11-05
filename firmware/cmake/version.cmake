# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# Writes version.h with the git description of the source tree. Run at every
# build; the file only changes when the description does.
#   cmake -DSRC=<dir> -DOUT=<file> -P version.cmake
#
# Firmware releases are tagged firmware-v<major>.<minor>.<patch>; other tags
# (the test files') are not looked at. The version is the description without
# the prefix: 1.0.0 at the tag, 1.0.0-3-gabc1234 after it, the commit hash
# without a firmware tag, each with -dirty for uncommitted changes. Source
# that is not its own git repository (an unpacked archive, say, even inside
# another repository's work tree) is "unknown".

set(version "unknown")
execute_process(
  COMMAND git rev-parse --show-toplevel
  WORKING_DIRECTORY ${SRC}
  OUTPUT_VARIABLE top
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
  RESULT_VARIABLE result)
if(result EQUAL 0 AND NOT top STREQUAL "")
  get_filename_component(top "${top}" REALPATH)
  get_filename_component(src "${SRC}" REALPATH)
  if("${top}" STREQUAL "${src}")
    execute_process(
      COMMAND git describe --tags --match firmware-v* --always --dirty --abbrev=7
      WORKING_DIRECTORY ${SRC}
      OUTPUT_VARIABLE described
      OUTPUT_STRIP_TRAILING_WHITESPACE
      ERROR_QUIET
      RESULT_VARIABLE result)
    if(result EQUAL 0 AND NOT described STREQUAL "")
      set(version "${described}")
    endif()
  endif()
endif()
string(REGEX REPLACE "^firmware-v" "" version "${version}")

file(WRITE ${OUT}.tmp "#define FW_VERSION \"${version}\"\n")
file(COPY_FILE ${OUT}.tmp ${OUT} ONLY_IF_DIFFERENT)
file(REMOVE ${OUT}.tmp)
