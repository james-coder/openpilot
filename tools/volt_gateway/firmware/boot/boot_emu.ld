/* NEVER FLASH: CPU harness without hardware startup/vector table. */
ENTRY(vgw_boot_emu_entry)
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 256K
  RAM (rw) : ORIGIN = 0x20000000, LENGTH = 64K
}
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > FLASH
  .data : { *(.data*) } > RAM
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) }
}
