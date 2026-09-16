/* STM32F413 reset mechanics. No application, recovery policy or keys here. */
.syntax unified
.cpu cortex-m4
.thumb
.section .vectors,"a",%progbits
.balign 512
.global vgw_vectors
vgw_vectors:
  .word __stack_top
  .word vgw_reset
  .rept 116
  .word vgw_early_fault
  .endr

.section .text.vgw_reset,"ax",%progbits
.thumb_func
.global vgw_reset
vgw_reset:
  cpsid i
  ldr r0, =__ramfunc_load
  ldr r1, =__ramfunc_start
  ldr r2, =__ramfunc_end
  bl copy_words
  ldr r0, =__data_load
  ldr r1, =__data_start
  ldr r2, =__data_end
  bl copy_words
  ldr r1, =__bss_start
  ldr r2, =__bss_end
  movs r0, #0
1:
  cmp r1, r2
  bhs 2f
  str r0, [r1], #4
  b 1b
2:
  ldr r0, =0xe000ed08
  ldr r1, =vgw_vectors
  str r1, [r0]
  dsb
  isb
  bl vgw_loader_main
  b vgw_early_fault
copy_words:
  cmp r1, r2
  bhs 3f
  ldr r3, [r0], #4
  str r3, [r1], #4
  b copy_words
3:
  bx lr

/* Flash resident: usable even before SRAM initialization. Request reset;
 * never feed IWDG or enable any controller. GPIO enables are active-low.
 * This is not a recovery service, and a persistent fault can reset-loop. */
.thumb_func
.global vgw_early_fault
vgw_early_fault:
  cpsid i
  ldr r0, =0x40023830
  ldr r1, [r0]
  orr r1, r1, #5
  str r1, [r0]
  ldr r1, [r0]
  ldr r0, =0x40020818
  ldr r1, =0x2002
  str r1, [r0]
  ldr r0, =0x40020018
  movs r1, #1
  str r1, [r0]
  ldr r0, =0x40023820
  ldr r1, [r0]
  orr r1, r1, #0x0e000000
  str r1, [r0]
  dsb
  ldr r0, =0xe000ed0c
  ldr r1, =0x05fa0004
  str r1, [r0]
  dsb
4:
  b 4b
