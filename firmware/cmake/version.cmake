# Writes version.h with the git description of the source tree. Run at every
# build; the file only changes when the description does.
#   cmake -DSRC=<dir> -DOUT=<file> -P version.cmake
#
# Firmware releases are tagged firmware-v<major>.<minor>.<patch>; other tags
# (the test files') are not looked at. The version is the description without
# the prefix: 1.0.0 at the tag, 1.0.0-3-gabc1234 after it, the commit hash
# without a firmware tag, each with -dirty for uncommitted changes.

execute_process(
  COMMAND git describe --tags --match firmware-v* --always --dirty --abbrev=7
  WORKING_DIRECTORY ${SRC}
  OUTPUT_VARIABLE version
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
  RESULT_VARIABLE result)
if(NOT result EQUAL 0 OR version STREQUAL "")
  set(version "unknown")
endif()
string(REGEX REPLACE "^firmware-v" "" version "${version}")

file(WRITE ${OUT}.tmp "#define FW_VERSION \"${version}\"\n")
file(COPY_FILE ${OUT}.tmp ${OUT} ONLY_IF_DIFFERENT)
file(REMOVE ${OUT}.tmp)
