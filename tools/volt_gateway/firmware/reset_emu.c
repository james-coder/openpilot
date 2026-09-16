#ifndef VGW_RESET_TEST
#error NEVER_FLASH reset harness requires VGW_RESET_TEST
#endif
#include <stdint.h>
volatile uint32_t vgw_reset_data = 0x12345678U;
volatile uint32_t vgw_reset_bss;
__attribute__((noinline,section(".ramfunc.reset_test")))
uint32_t vgw_reset_ram(uint32_t x) { return x ^ 0xa5a5a5a5U; }
__attribute__((noinline)) void vgw_reset_done(void) { __asm__ volatile("nop"); }
void vgw_loader_main(void) {
  vgw_reset_bss = vgw_reset_ram(vgw_reset_data) + vgw_reset_bss;
  vgw_reset_done();
}
