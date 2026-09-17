/* NEVER FLASH: physical-binding CPU harness, no recovery/vector startup. */
ENTRY(vgw_platform_entry)
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 256K
  RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}
SECTIONS {
  .text : { *(.text*) *(.rodata*) } > FLASH
  .ramfunc : { *(.ramfunc*) } > RAM AT> FLASH
  .data : { *(.data*) } > RAM AT> FLASH
  .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) }
}
