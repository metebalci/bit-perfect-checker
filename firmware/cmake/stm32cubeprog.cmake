# A "flash" target that writes the firmware with ST's STM32CubeProgrammer
# (STM32_Programmer_CLI), shared by the STM32 families. It names the ST-LINK
# by its serial number, STLINK_SN, so that it never writes to another board
# that happens to be connected; without STLINK_SN it refuses to run.

set(STLINK_SN "" CACHE STRING "Serial number of the ST-LINK to flash through")

# From its default install folders
find_program(STM32_PROGRAMMER STM32_Programmer_CLI
  HINTS
    $ENV{HOME}/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin
    /Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/STM32CubeProgrammer.app/Contents/MacOs/bin)

# A "flash" target for the firmware image <bin>, which <target> builds
function(family_flash_target target bin)
  if(NOT STM32_PROGRAMMER)
    return()
  endif()
  if(STLINK_SN)
    add_custom_target(flash
      COMMAND ${STM32_PROGRAMMER} -c port=SWD sn=${STLINK_SN} mode=UR -w ${bin} 0x08000000 -v -rst
      DEPENDS ${target}
      USES_TERMINAL VERBATIM)
  else()
    add_custom_target(flash
      COMMAND ${CMAKE_COMMAND} -E echo
              "flash: STLINK_SN is not set. List the probes with `STM32_Programmer_CLI -l st-link`, then set the board's with `cmake -DSTLINK_SN=<serial number> <build folder>`."
      COMMAND ${CMAKE_COMMAND} -E false
      USES_TERMINAL VERBATIM)
  endif()
endfunction()
