/* STM32F723xE: 512 KB flash (on the AXI bus); 64 KB DTCM, then SRAM1 and
   SRAM2, which form one 192 KB block. The stack takes the bottom 16 KB of
   DTCM, so that an overflow runs off the start of RAM instead of into data;
   .data and .bss go in SRAM1 and SRAM2. */

ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 512K
  DTCM  (rw) : ORIGIN = 0x20000000, LENGTH = 64K
  RAM   (rw) : ORIGIN = 0x20010000, LENGTH = 192K
}

_stack_size = 16K;
_sstack = ORIGIN(DTCM);
_estack = _sstack + _stack_size;

SECTIONS
{
  .isr_vector :
  {
    KEEP(*(.isr_vector))
  } > FLASH

  .text :
  {
    *(.text .text.*)
    *(.glue_7) *(.glue_7t)
    *(.eh_frame)
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
  } > FLASH

  .rodata :
  {
    *(.rodata .rodata.*)
    . = ALIGN(4);
  } > FLASH

  .ARM.exidx :
  {
    *(.ARM.exidx* .gnu.linkonce.armexidx.*)
  } > FLASH

  .preinit_array :
  {
    PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*))
    PROVIDE_HIDDEN(__preinit_array_end = .);
  } > FLASH

  .init_array :
  {
    PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*)))
    KEEP(*(.init_array*))
    PROVIDE_HIDDEN(__init_array_end = .);
  } > FLASH

  .fini_array :
  {
    PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*)))
    KEEP(*(.fini_array*))
    PROVIDE_HIDDEN(__fini_array_end = .);
  } > FLASH

  /* The stack grows down from _estack; this only reserves its room */
  .stack (NOLOAD) :
  {
    . = . + _stack_size;
  } > DTCM

  /* Copied from flash to RAM by the startup code */
  _sidata = LOADADDR(.data);
  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data .data.*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT > FLASH

  /* Zeroed by the startup code */
  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    __bss_start__ = _sbss;
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
    __bss_end__ = _ebss;
  } > RAM

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

ASSERT(ADDR(.stack) == _sstack && ADDR(.stack) + SIZEOF(.stack) == _estack,
       "the stack must fill the bottom of DTCM up to _estack")
