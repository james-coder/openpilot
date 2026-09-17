/* USB-only bench diagnostic, SRAM-loaded: never flash this image.
 * No keys, flash driver, or CAN transmit routine is linked. */
#include "white_usb.h"
#include "white_clock.h"
#include "white_platform.h"
#include "white_rng.h"
#include "white_can.h"
static vgw_white_startup startup;
static vgw_white_clock clock;
static vgw_white_rng rng;
static vgw_white_can can;
static uint32_t step;
static uint32_t traced_read(void *ctx,uint32_t address) {
  (void)ctx;
  uint32_t value=*(volatile uint32_t *)(uintptr_t)address;
  if (address>=0x40006400U && address<0x40007000U) {
    volatile uint32_t *p=(volatile uint32_t *)0x2001c400U;
    p[0]=0x314e4356; p[1]=address; p[2]=value; p[5]=0x56434e31;
  }
  return value;
}
static uint16_t traced_read16(void *ctx,uint32_t address) {
  (void)ctx; return *(volatile uint16_t *)(uintptr_t)address;
}
static void traced_write(void *ctx,uint32_t address,uint32_t value) {
  (void)ctx;
  if (address>=0x40006400U && address<0x40007000U) {
    volatile uint32_t *p=(volatile uint32_t *)0x2001c400U;
    p[3]=address; p[4]=value;
  }
  *(volatile uint32_t *)(uintptr_t)address=value;
  __asm__ volatile("dsb" ::: "memory");
}
static void mark(uint32_t n) {
  volatile uint32_t *p=(volatile uint32_t *)0x2001c000U;
  step=n; p[0]=0x56505231; p[1]=n; p[2]=~n; p[3]=0x31475052;
  __asm__ volatile("dsb" ::: "memory");
}
unsigned vgw_ram_probe_report(uint8_t out[64]) {
  uint32_t values[]={0x56505231,step,vgw_white_startup_now(&startup),can.ready,can.failed};
  for (unsigned i=0;i<5;i++) for (unsigned j=0;j<4;j++) out[i*4+j]=(uint8_t)(values[i]>>(24-8*j));
  return 20;
}
void vgw_loader_main(void) {
  static const vgw_white_mmio trace_io={0,traced_read,traced_read16,traced_write};
  const vgw_white_mmio *io=&trace_io;
  volatile uint32_t *trace=(volatile uint32_t *)0x2001c400U;
  for (unsigned i=0;i<6;i++) trace[i]=0;
  mark(201);
  if (!vgw_white_startup_init(&startup,io)) {
    mark(0x20100000U | ((uint32_t)startup.watchdog.started<<19) |
      ((uint32_t)startup.watchdog.failed<<18) | ((io->read32(io->ctx,0x4000300cU)&3U)<<16) |
      ((io->read32(io->ctx,0x40003004U)&7U)<<12) | (io->read32(io->ctx,0x40003008U)&4095U));
    vgw_white_physical_reset();
  }
  mark(202);
  if (!vgw_white_clock_init(&clock,&startup)) vgw_white_physical_reset();
  mark(203);
  if (!vgw_white_rng_init(&rng,&clock)) vgw_white_physical_reset();
  uint8_t nonce[32];
  mark(204);
  if (!vgw_white_rng_nonce(&rng,nonce)) vgw_white_physical_reset();
  const vgw_white_can_config config={3,3,0,0};
  mark(205);
  if (!vgw_white_can_init(&can,&clock,&config)) vgw_white_physical_reset();
  mark(206);
  if (!vgw_white_usb_init(&startup,false)) vgw_white_physical_reset();
  mark(207);
  uint32_t start=vgw_white_startup_now(&startup);
  vgw_status_led led; vgw_led_init(&led,start); vgw_led_set(&led,VGW_LED_PROBE,255,0,start);
  for (;;) {
    uint32_t now=vgw_white_startup_now(&startup);
    if (!vgw_white_can_poll(&can,0,0)) { mark(208); vgw_white_physical_reset(); }
    if (!vgw_white_usb_poll() || !vgw_white_rng_health(&rng)) { mark(209); vgw_white_physical_reset(); }
    vgw_white_watchdog_progress(&startup.watchdog,VGW_PROGRESS_ALL);
    if (!vgw_white_watchdog_service(&startup.watchdog,now)) { mark(210); vgw_white_physical_reset(); }
    vgw_white_led(io,vgw_led_sample(&led,now));
    if ((uint32_t)(now-start)>=60000U) { mark(211); vgw_white_physical_reset(); }
  }
}
