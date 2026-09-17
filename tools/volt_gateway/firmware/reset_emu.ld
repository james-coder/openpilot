/* NEVER FLASH: reset mechanics harness, not a complete trusted loader. */
ENTRY(vgw_reset)
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 256K
  RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}
__stack_top = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {
  .vectors : { KEEP(*(.vectors)) } > FLASH
  .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
  .ramfunc : {
    __ramfunc_start = .; *(.ramfunc*) . = ALIGN(4); __ramfunc_end = .;
  } > RAM AT> FLASH
  __ramfunc_load = LOADADDR(.ramfunc);
  .data : {
    __data_start = .; *(.data*) . = ALIGN(4); __data_end = .;
  } > RAM AT> FLASH
  __data_load = LOADADDR(.data);
  .bss (NOLOAD) : {
    __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end = .;
  } > RAM
  ASSERT(__bss_end <= __stack_top - 8192, "insufficient reserved stack")
  ASSERT(SIZEOF(.vectors) == 472, "incorrect F413 vector count")
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) }
}
