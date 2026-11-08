/* SPDX-License-Identifier: MIT */
/* Copyright (c) 2026 Mete Balci */
/* STM32F723xE for the BPC1 generator (USB host), as stm32f723xe.ld for the
   checker except for the USB host's code and buffers.
   512 KB flash, 16 KB ITCM RAM, 64 KB DTCM, then SRAM1 and SRAM2, which
   form one 192 KB block.
   - The image is linked at the flash's ITCM interface (0x00200000), where
     code and read-only data go through the ART accelerator and prefetch; it
     is written through the AXI address 0x08000000, the same flash.
   - The USB interrupt's code runs from ITCM RAM (zero wait states); the
     first 32 bytes stay unused, for the MPU's NULL guard.
   - The stack takes the bottom 16 KB of DTCM, so that an overflow runs off
     the start of RAM instead of into data; above it the USB buffers. .data
     and .bss go in SRAM1 and SRAM2. */

ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx) : ORIGIN = 0x00200000, LENGTH = 512K
  ITCM  (rx) : ORIGIN = 0x00000020, LENGTH = 16K - 32
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

  /* The USB interrupt's code: TinyUSB's DWC2 host driver, memcpy and the
     functions marked FAMILY_ISR_CODE (family.h). Copied from flash by
     family_core_init. */
  .itcm :
  {
    . = ALIGN(4);
    _sitcm = .;
    *hcd_dwc2.c.obj(.text .text.*)
    *:*memcpy*.o(.text .text.*)
    *(.itcm_text .itcm_text.*)
    . = ALIGN(4);
    _eitcm = .;
  } > ITCM AT > FLASH
  _siitcm = LOADADDR(.itcm);

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

  /* TinyUSB's buffers (CFG_TUH_MEM_SECTION), zeroed by family_core_init */
  .dtcm_usb (NOLOAD) :
  {
    . = ALIGN(32);
    _sdtcm_usb = .;
    *(.dtcm_usb .dtcm_usb.*)
    . = ALIGN(4);
    _edtcm_usb = .;
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
ASSERT(_sdtcm_usb >= _estack && _edtcm_usb <= ORIGIN(DTCM) + LENGTH(DTCM),
       "the USB buffers must be in DTCM, above the stack")
/* The interrupt path must be in ITCM RAM: a renamed object file or libc
   member would otherwise leave it in flash, silently slower */
ASSERT(hcd_int_handler >= _sitcm && hcd_int_handler < _eitcm, "hcd_int_handler must be in .itcm")
ASSERT(memcpy >= _sitcm && memcpy < _eitcm, "memcpy must be in .itcm")
ASSERT(load_isr >= _sitcm && load_isr < _eitcm, "load_isr must be in .itcm")
