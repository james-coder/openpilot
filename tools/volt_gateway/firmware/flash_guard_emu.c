#ifndef VGW_FLASH_GUARD_TEST
#error NEVER_FLASH guard harness requires VGW_FLASH_GUARD_TEST
#endif
#include "recovery_flash.h"
#include "white_platform.h"
#define RAM __attribute__((section(".ramfunc.guard_test"),noinline))
vgw_recovery_service vgw_guard_test_recovery;
vgw_recovery_flash vgw_guard_test_state;
volatile uint32_t vgw_guard_test_mode,vgw_guard_test_received;
vgw_white_platform vgw_guard_test_platform;
vgw_white_flash vgw_guard_test_flash;
volatile uint32_t vgw_guard_test_erase;
static RAM bool sample(void *ctx,uint64_t now,uint64_t *sampled,bool *allowed) {
  (void)ctx; *sampled=vgw_guard_test_mode==2 ? now-31 : now; *allowed=vgw_guard_test_mode!=1; return true;
}
static RAM void receive(void *ctx,const vgw_frame *f) { (void)ctx; (void)f; vgw_guard_test_received++; }
RAM void vgw_guard_test_begin(void) { __asm__ volatile("nop"); }
RAM void vgw_guard_test_end(void) { __asm__ volatile("nop"); }
static RAM bool exercise(void) {
  vgw_guard_test_begin();
  vgw_recovery_flash_service(&vgw_guard_test_state);
  bool ok=vgw_white_watchdog_service(&vgw_guard_test_state.runtime->startup.watchdog,
    vgw_white_startup_now(&vgw_guard_test_state.runtime->startup));
  vgw_guard_test_end();
  return ok && !vgw_guard_test_state.failed;
}
bool vgw_guard_test(vgw_white_runtime *runtime) {
  /* Synthetic grant and safety source only. Real signed-grant integration is
   * tested separately; these fixture permissions are NEVER board defaults. */
  vgw_recovery_service *s=&vgw_guard_test_recovery;
  s->ready=s->active=true; s->expires=10000;
  s->authority.granted=true; s->authority.phase=VGW_PROGRAM; s->authority.grant_until=10000;
  if (!vgw_recovery_flash_init(&vgw_guard_test_state,runtime,s,sample,receive,0)) return false;
  if (vgw_guard_test_erase) {
    vgw_guard_test_platform=(vgw_white_platform){.startup=&runtime->startup,
      .permit=vgw_recovery_flash_permit,.service=vgw_recovery_flash_service,.context=&vgw_guard_test_state};
    vgw_flash_io io;
    if (!vgw_white_physical_flash(&vgw_guard_test_platform,&io) || !vgw_white_flash_init(&vgw_guard_test_flash,&io,1,true)) return false;
    if (vgw_guard_test_erase==2) {
      static const uint8_t payload[16]={0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
      return vgw_white_flash_program(&vgw_guard_test_flash,0,payload,sizeof(payload));
    }
    return vgw_white_flash_erase(&vgw_guard_test_flash,0);
  }
  return exercise();
}
