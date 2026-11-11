/* SPDX-License-Identifier: MIT */
/* Copyright (c) 2026 Mete Balci */
/* STM32F723xE: 512 KB flash, 16 KB ITCM RAM, 64 KB DTCM, then SRAM1 and
   SRAM2, which form one 192 KB block.
   - The image is linked at the flash's ITCM interface (0x00200000), where
     code and read-only data go through the ART accelerator and prefetch; it
     is written through the AXI address 0x08000000, the same flash.
   - The USB interrupt's code runs from ITCM RAM (zero wait states); the
     first 32 bytes stay unused, for the MPU's NULL guard.
   - The stack takes the bottom 16 KB of DTCM, so that an overflow runs off
     the start of RAM instead of into data; above it the USB data: the
     stream's class memory, the stream's queue, and u2ps's DMA memory and
     packet-path state. .data and .bss go in SRAM1 and SRAM2. */

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

  /* The USB interrupt's code: u2ps's packet path (section .text.u2ps_fast:
     the DWC2 port's interrupt handler, its stream passes, the device
     stack's packet, hole and release functions and the feedback value),
     memcpy and memset, and the functions marked FAMILY_ISR_CODE
     (family.h). Copied from flash by family_core_init. */
  .itcm :
  {
    . = ALIGN(4);
    _sitcm = .;
    *(.text.u2ps_fast)
    *:*memcpy*.o(.text .text.*)
    *:*memset*.o(.text .text.*)
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

  /* The USB data, zeroed by family_core_init: the stream's class memory
     (FAMILY_CLASS_MEM, u2ps_glue.c), the app's other USB data
     (FAMILY_USB_DATA: the stream's queue, stream.c), u2ps's DMA memory
     (.bss.u2ps_dma) and its packet-path state (.bss.u2ps_fast: the DWC2
     port's and the device stack's) */
  .dtcm_usb (NOLOAD) :
  {
    . = ALIGN(32);
    _sdtcm_usb = .;
    _sclass_mem = .;
    *(.dtcm_usb.class_mem)
    _eclass_mem = .;
    *(.dtcm_usb .dtcm_usb.*)
    *(.bss.u2ps_dma)
    _su2ps_fast = .;
    *(.bss.u2ps_fast)
    _eu2ps_fast = .;
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
/* The class memory is static, so its section stands for it: at least 40
   buffers of 800 bytes, the stride of 32/4 at 768 kHz (u2ps_glue.c) */
ASSERT(_eclass_mem - _sclass_mem >= 40 * 800, "the class memory (u2ps_glue.c) must be in .dtcm_usb")
/* u2ps's state is checked by its section, not by name: with U2PS_LTO its
   variables become local symbols, which the script cannot name */
ASSERT(_eu2ps_fast > _su2ps_fast, "u2ps's packet-path state (.bss.u2ps_fast) must be in .dtcm_usb")

/* The interrupt path must be in ITCM RAM: a renamed object file or libc
   member would otherwise leave it in flash, silently slower */
ASSERT(u2ps_dwc2_dcd_irq >= _sitcm && u2ps_dwc2_dcd_irq < _eitcm, "u2ps_dwc2_dcd_irq must be in .itcm")
ASSERT(u2ps_dev_release >= _sitcm && u2ps_dev_release < _eitcm, "u2ps_dev_release must be in .itcm")
ASSERT(memcpy >= _sitcm && memcpy < _eitcm, "memcpy must be in .itcm")
ASSERT(memset >= _sitcm && memset < _eitcm, "memset must be in .itcm")
/* The app's part, marked FAMILY_ISR_CODE: the vector, u2ps's callbacks in
   the USB interrupt (u2ps_glue.c; the static ev_put, ev_commit and
   ev_simple are marked too), the stream's (stream.c) and the load
   figures'. */
ASSERT(OTG_HS_IRQHandler >= _sitcm && OTG_HS_IRQHandler < _eitcm, "OTG_HS_IRQHandler must be in .itcm")
ASSERT(u2ps_app_on_packet >= _sitcm && u2ps_app_on_packet < _eitcm, "u2ps_app_on_packet must be in .itcm")
ASSERT(u2ps_app_on_hole >= _sitcm && u2ps_app_on_hole < _eitcm, "u2ps_app_on_hole must be in .itcm")
ASSERT(u2ps_app_on_pause >= _sitcm && u2ps_app_on_pause < _eitcm, "u2ps_app_on_pause must be in .itcm")
ASSERT(u2ps_app_on_request >= _sitcm && u2ps_app_on_request < _eitcm, "u2ps_app_on_request must be in .itcm")
ASSERT(u2ps_app_on_sampling_frequency >= _sitcm && u2ps_app_on_sampling_frequency < _eitcm,
       "u2ps_app_on_sampling_frequency must be in .itcm")
ASSERT(u2ps_app_on_configured >= _sitcm && u2ps_app_on_configured < _eitcm, "u2ps_app_on_configured must be in .itcm")
ASSERT(u2ps_app_on_stream_start >= _sitcm && u2ps_app_on_stream_start < _eitcm,
       "u2ps_app_on_stream_start must be in .itcm")
ASSERT(u2ps_app_on_stream_stop >= _sitcm && u2ps_app_on_stream_stop < _eitcm,
       "u2ps_app_on_stream_stop must be in .itcm")
ASSERT(u2ps_app_on_fault >= _sitcm && u2ps_app_on_fault < _eitcm, "u2ps_app_on_fault must be in .itcm")
ASSERT(u2ps_app_on_reset >= _sitcm && u2ps_app_on_reset < _eitcm, "u2ps_app_on_reset must be in .itcm")
ASSERT(u2ps_app_on_suspend >= _sitcm && u2ps_app_on_suspend < _eitcm, "u2ps_app_on_suspend must be in .itcm")
ASSERT(u2ps_app_on_resume >= _sitcm && u2ps_app_on_resume < _eitcm, "u2ps_app_on_resume must be in .itcm")
ASSERT(u2ps_app_on_control >= _sitcm && u2ps_app_on_control < _eitcm, "u2ps_app_on_control must be in .itcm")
ASSERT(stream_packet_isr >= _sitcm && stream_packet_isr < _eitcm, "stream_packet_isr must be in .itcm")
ASSERT(stream_hole_isr >= _sitcm && stream_hole_isr < _eitcm, "stream_hole_isr must be in .itcm")
ASSERT(stream_restart_isr >= _sitcm && stream_restart_isr < _eitcm, "stream_restart_isr must be in .itcm")
ASSERT(load_isr >= _sitcm && load_isr < _eitcm, "load_isr must be in .itcm")
ASSERT(load_fifo >= _sitcm && load_fifo < _eitcm, "load_fifo must be in .itcm")
