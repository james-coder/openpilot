/* CPU test only. Intentionally lacks boot/recovery/provisioning. NEVER FLASH. */
#ifndef VGW_PLATFORM_TEST
#error This harness is not board firmware
#endif
#include "white_platform.h"
static vgw_white_startup startup;
static vgw_white_platform platform;
static vgw_flash_io flash;
volatile uint32_t vgw_platform_result, vgw_platform_mode, vgw_platform_services;
void *memset(void *ptr, int value, size_t size) {
  uint8_t *p=ptr; for (size_t i=0;i<size;i++) p[i]=(uint8_t)value; return ptr;
}
#define RAM __attribute__((section(".ramfunc.test"), noinline))
static RAM bool allowed(void *ctx) { (void)ctx; return true; }
static RAM void work(void *ctx) {
  (void)ctx; vgw_platform_services++;
  /* Synthetic completion only; real owner must execute these tasks. */
  vgw_white_watchdog_progress(&startup.watchdog,VGW_PROGRESS_ALL);
}
__attribute__((noreturn,noinline)) void vgw_platform_done(void) { for (;;) __asm__ volatile("nop"); }
void vgw_platform_entry(void) {
  startup.ready=true; startup.watchdog.started=true;
  startup.watchdog.io=*vgw_white_physical_mmio();
  platform=(vgw_white_platform){.startup=&startup,.permit=allowed,.service=work};
  if (!vgw_white_physical_flash(&platform,&flash)) { vgw_platform_result=99; vgw_platform_done(); }
  if (vgw_platform_mode==0) {
    uint8_t value=0;
    bool ok=flash.reg_write(flash.ctx,0x40023c00U,5) && !flash.reg_write(flash.ctx,0x40023c08U,1) &&
      !flash.program8(flash.ctx,0x08000000U,0) && flash.program8(flash.ctx,0x08040000U,0xa5) &&
      flash.read(flash.ctx,0x08040000U,&value,1) && value==0xa5 && !flash.read(flash.ctx,0x08100000U,&value,1);
    vgw_platform_result=ok;
  } else if (vgw_platform_mode==1) {
    uint32_t before, during, after;
    __asm__ volatile("mrs %0, primask" : "=r"(before));
    bool ok=flash.enter(flash.ctx);
    __asm__ volatile("mrs %0, primask" : "=r"(during));
    ok=ok && !flash.enter(flash.ctx);
    flash.leave(flash.ctx);
    __asm__ volatile("mrs %0, primask" : "=r"(after));
    vgw_platform_result=ok && during==1 && before==after;
  } else if (vgw_platform_mode==2) {
    flash.service(flash.ctx);
    vgw_platform_result=vgw_platform_services==1;
  } else if (vgw_platform_mode==3) {
    startup.watchdog.failed=true;
    flash.service(flash.ctx);
    vgw_platform_result=98; /* must never return from reset */
  } else if (vgw_platform_mode==4) {
    platform.entered=true;
    vgw_platform_result=!vgw_white_physical_flash(&platform,&flash);
  } else {
    vgw_boot_choice choice={0,0x08040200U,0x2001e000U,0x08040209U};
    if (vgw_platform_mode==6) choice.stack_pointer=0x20030000U;
    vgw_platform_result=!vgw_white_physical_handoff(&choice);
  }
  vgw_platform_done();
}
