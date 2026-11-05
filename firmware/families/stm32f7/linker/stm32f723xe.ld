/* STM32F723xE: 512 KB flash, 16 KB ITCM RAM, 64 KB DTCM, then SRAM1 and
   SRAM2, which form one 192 KB block.
   - The image is linked at the flash's ITCM interface (0x00200000), where
     code and read-only data go through the ART accelerator and prefetch; it
     is written through the AXI address 0x08000000, the same flash.
   - The USB interrupt's code runs from ITCM RAM (zero wait states); the
     first 32 bytes stay unused, for the MPU's NULL guard.
   - The stack takes the bottom 16 KB of DTCM, so that an overflow runs off
     the start of RAM instead of into data; above it the USB buffers and the
     receive FIFO's storage. .data and .bss go in SRAM1 and SRAM2. */

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

  /* The USB interrupt's code: TinyUSB's DWC2 driver, device core, audio
     class and FIFO, and memcpy. Copied from flash by family_core_init. */
  .itcm :
  {
    . = ALIGN(4);
    _sitcm = .;
    *dcd_dwc2.c.obj(.text .text.*)
    *audio_device.c.obj(.text .text.*)
    *tusb_fifo.c.obj(.text .text.*)
    *usbd.c.obj(.text .text.*)
    *:*memcpy*.o(.text .text.*)
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

  /* TinyUSB's buffers (CFG_TUD_MEM_SECTION) and the audio FIFO's storage,
     zeroed by family_core_init */
  .dtcm_usb (NOLOAD) :
  {
    . = ALIGN(32);
    _sdtcm_usb = .;
    *(.dtcm_usb .dtcm_usb.*)
    *(.bss.ep_out_sw_buf)  /* audio_device.c */
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
/* ep_out_sw_buf is static, so its size stands for it: the FIFO alone is
   CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ (32760) bytes */
ASSERT(_edtcm_usb - _sdtcm_usb >= 32760,
       "the audio FIFO's storage (ep_out_sw_buf, audio_device.c) must be in .dtcm_usb")

/* The interrupt path must be in ITCM RAM: a renamed object file or libc
   member would otherwise leave it in flash, silently slower */
ASSERT(dcd_int_handler >= _sitcm && dcd_int_handler < _eitcm, "dcd_int_handler must be in .itcm")
ASSERT(dcd_event_handler >= _sitcm && dcd_event_handler < _eitcm, "dcd_event_handler must be in .itcm")
ASSERT(audiod_xfer_isr >= _sitcm && audiod_xfer_isr < _eitcm, "audiod_xfer_isr must be in .itcm")
ASSERT(usbd_edpt_xfer >= _sitcm && usbd_edpt_xfer < _eitcm, "usbd_edpt_xfer must be in .itcm")
ASSERT(tu_fifo_write_n_access_mode >= _sitcm && tu_fifo_write_n_access_mode < _eitcm,
       "tu_fifo_write_n_access_mode must be in .itcm")
ASSERT(memcpy >= _sitcm && memcpy < _eitcm, "memcpy must be in .itcm")
