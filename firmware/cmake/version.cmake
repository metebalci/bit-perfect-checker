# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
# Writes version.h with the firmware version. Run at configure time and at
# every build; the file only changes when the version does.
#   cmake -DSRC=<dir> -DOUT=<file> -P version.cmake
#
# firmware/VERSION holds the version being developed, e.g. 1.2.0. The
# version is v<VERSION>-<commit> at every commit, a release's tag included,
# with git's short hash at its default length and -dirty for uncommitted
# changes; v<VERSION>-unknown for source that is not its own git repository
# (an unpacked archive, say, even inside another repository's work tree).
# Firmware releases are tagged firmware-v<VERSION>: at such a tag whose
# number is not VERSION, the build stops. Other tags (the test files') and
# pre-release tags (firmware-v<VERSION>-<suffix>, e.g. -rc1) are not looked
# at.

file(STRINGS ${CMAKE_CURRENT_LIST_DIR}/../VERSION release LIMIT_COUNT 1)
string(STRIP "${release}" release)
if(NOT release MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+$")
  message(FATAL_ERROR "firmware/VERSION holds '${release}', not <major>.<minor>.<patch>")
endif()

set(version "v${release}-unknown")
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
    # firmware-v<x>-<n>-g<hash> after (or at, n = 0) the last firmware tag,
    # else the hash alone; each with -dirty
    execute_process(
      COMMAND git describe --tags --match firmware-v* --exclude firmware-v*-* --always --long --dirty
      WORKING_DIRECTORY ${SRC}
      OUTPUT_VARIABLE described
      OUTPUT_STRIP_TRAILING_WHITESPACE
      ERROR_QUIET
      RESULT_VARIABLE result)
    if(result EQUAL 0 AND NOT described STREQUAL "")
      if(described MATCHES "^firmware-v([^-]+)-0-g[0-9a-f]+(-dirty)?$")
        # At a firmware tag
        if(NOT CMAKE_MATCH_1 STREQUAL release)
          message(FATAL_ERROR "At tag firmware-v${CMAKE_MATCH_1}, but firmware/VERSION is ${release}")
        endif()
      endif()
      if(described MATCHES "-g([0-9a-f]+)(-dirty)?$")
        set(version "v${release}-${CMAKE_MATCH_1}${CMAKE_MATCH_2}")
      elseif(described MATCHES "^([0-9a-f]+)(-dirty)?$")
        set(version "v${release}-${CMAKE_MATCH_1}${CMAKE_MATCH_2}")
      else()
        message(FATAL_ERROR "No commit in git describe's '${described}'")
      endif()
    endif()
  endif()
endif()

file(WRITE ${OUT}.tmp "#define FW_VERSION \"${version}\"\n")
file(COPY_FILE ${OUT}.tmp ${OUT} ONLY_IF_DIFFERENT)
file(REMOVE ${OUT}.tmp)
