#ifndef VGW_RUNTIME_TEST
#error NEVER_FLASH runtime harness requires VGW_RUNTIME_TEST
#endif
#include "white_runtime.h"
#include "white_platform.h"
vgw_white_runtime vgw_runtime_test_state;
volatile uint32_t vgw_runtime_test_result;
/* Freestanding byte routines for the harness. Real linker map/call-graph
 * review is still required before flash-busy use. */
void *memset(void *p,int c,size_t n) {
  uint8_t *d=p; for (size_t i=0;i<n;i++) d[i]=(uint8_t)c; return p;
}
void *memcpy(void *p,const void *q,size_t n) {
  uint8_t *d=p; const uint8_t *s=q; for (size_t i=0;i<n;i++) d[i]=s[i]; return p;
}
static bool no_commands(void *ctx,vgw_white_runtime *s) { (void)ctx; (void)s; return true; }
__attribute__((noinline)) void vgw_runtime_test_done(void) { __asm__ volatile("nop"); }
void vgw_loader_main(void) {
  /* Synthetic unconnected test only. No production bus/IDs or keys. */
  const vgw_white_can_config config={3,3,0,0};
  vgw_white_runtime *s=&vgw_runtime_test_state;
  if (!vgw_white_runtime_init(s,vgw_white_physical_mmio(),&config,0x600)) vgw_runtime_test_result=1;
  else if (!vgw_white_runtime_step(s,no_commands,0)) vgw_runtime_test_result=2;
  else vgw_runtime_test_result=3;
  vgw_runtime_test_done();
}
