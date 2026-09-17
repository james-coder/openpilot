/* CPU-emulation harness ONLY. Not a White Panda linker layout or boot image. */
ENTRY(vgw_emu_entry)
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
  RAM (rw) : ORIGIN = 0x20000000, LENGTH = 96K
}
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > FLASH
  .data : { *(.data*) } > RAM
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) }
}
