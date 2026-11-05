# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# Writes version.h with the firmware version. Run at configure time and at
# every build; the file only changes when the version does.
#   cmake -DSRC=<dir> -DOUT=<file> -P version.cmake
#
# firmware/VERSION holds the version being developed, e.g. 1.1.0. Firmware
# releases are tagged firmware-v<VERSION>; other tags (the test files') are
# not looked at. The version is <VERSION> at its tag, <VERSION>-<commit>
# (a pre-release) at any other commit, each with -dirty for uncommitted
# changes; <VERSION>-unknown for source that is not its own git repository
# (an unpacked archive, say, even inside another repository's work tree). A
# firmware tag whose number is not VERSION stops the build.

file(STRINGS ${CMAKE_CURRENT_LIST_DIR}/../VERSION release LIMIT_COUNT 1)
string(STRIP "${release}" release)
if(NOT release MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+$")
  message(FATAL_ERROR "firmware/VERSION holds '${release}', not <major>.<minor>.<patch>")
endif()

set(version "${release}-unknown")
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
      if(described MATCHES "^firmware-v([^-]+)(-dirty)?$")
        # At a firmware tag
        if(NOT CMAKE_MATCH_1 STREQUAL release)
          message(FATAL_ERROR "At tag firmware-v${CMAKE_MATCH_1}, but firmware/VERSION is ${release}")
        endif()
        set(version "${release}${CMAKE_MATCH_2}")
      elseif(described MATCHES "-g([0-9a-f]+)(-dirty)?$")
        # After a firmware tag: firmware-v<x>-<n>-g<hash>
        set(version "${release}-${CMAKE_MATCH_1}${CMAKE_MATCH_2}")
      else()
        # No firmware tag: the hash alone
        set(version "${release}-${described}")
      endif()
    endif()
  endif()
endif()

file(WRITE ${OUT}.tmp "#define FW_VERSION \"${version}\"\n")
file(COPY_FILE ${OUT}.tmp ${OUT} ONLY_IF_DIFFERENT)
file(REMOVE ${OUT}.tmp)
