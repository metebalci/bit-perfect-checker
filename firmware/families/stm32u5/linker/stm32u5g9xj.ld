/* SPDX-License-Identifier: MIT */
/* Copyright (c) 2026 Mete Balci */
/* STM32U5G9ZJ: 4 MB flash; SRAM1, 2, 3 and 5 form one 2496 KB block, as on
   the STM32U5A5ZJ. SRAM6 (512 KB) follows it and holds only the display's
   frame buffer, so that the LTDC reads a bank of its own. */

ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 4096K
  RAM   (rw) : ORIGIN = 0x20000000, LENGTH = 2496K
  FB    (rw) : ORIGIN = 0x20270000, LENGTH = 512K
}

_stack_size = 16K;
_estack = ORIGIN(RAM) + LENGTH(RAM);
_sstack = _estack - _stack_size;  /* MSPLIM */

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

  /* The USB data that needs no zero start (FAMILY_CLASS_MEM,
     FAMILY_USB_DATA, family.h): the stream's class memory and queue, about
     1 MB. Not zeroed by the startup code, which runs at the reset clock and
     would delay the attach. */
  .noinit_usb (NOLOAD) :
  {
    . = ALIGN(4);
    _snoinit_usb = .;
    _sclass_mem = .;
    *(.noinit_usb.class_mem)
    _eclass_mem = .;
    *(.noinit_usb)
    . = ALIGN(4);
    _enoinit_usb = .;
  } > RAM

  /* The stack grows down from _estack; this only reserves its room */
  .stack (NOLOAD) :
  {
    . = ALIGN(8);
    . = . + _stack_size;
  } > RAM

  /* The frame buffer: never loaded or zeroed by the startup code */
  .framebuffer (NOLOAD) :
  {
    *(.framebuffer)
  } > FB

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

/* The class memory is static, so its section stands for it: at least 40
   buffers of 776 bytes, the stride of 32/4 at 768 kHz (u2ps_glue.c), out
   of .bss, which the startup code zeroes */
ASSERT(_eclass_mem - _sclass_mem >= 40 * 776, "the class memory (u2ps_glue.c) must be in .noinit_usb")
ASSERT(_snoinit_usb >= _ebss, ".noinit_usb must lie after .bss")

ASSERT(_estack <= ORIGIN(FB), "RAM reaches into SRAM6, the frame buffer's bank")
ASSERT(SIZEOF(.framebuffer) <= LENGTH(FB), "The frame buffer does not fit in SRAM6")
