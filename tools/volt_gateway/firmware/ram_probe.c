/* Volatile USB-only board inspection. NEVER FLASH. No CAN driver, flash driver,
 * update authority or pairing material is linked. Power cycle restores flash. */
#include "white_usb.h"
#include "white_clock.h"
#include "white_platform.h"
#include "white_safety.h"
static vgw_white_startup startup;
static vgw_white_clock clock;
static vgw_white_can silent;
static vgw_white_safety safety;
static uint32_t revision;
static uint32_t rd(uint32_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static void wr(uint32_t a,uint32_t v) { *(volatile uint32_t *)(uintptr_t)a=v; __asm__ volatile("dsb" ::: "memory"); }
static void stage(uint32_t n) {
  /* Original application's backed-up startup clears through 0x2001b470.
   * Keep this marker above that range and below either image's stack. */
  wr(0x2001c000,0x56505231); wr(0x2001c004,n); wr(0x2001c008,~n); wr(0x2001c00c,0x31475052);
}
static void put(uint8_t *p,uint32_t v) { for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(v>>(24-8*i)); }
unsigned vgw_ram_probe_report(uint8_t out[64]) {
  uint32_t fields[]={0x56505231,rd(0xe0042000),*(volatile uint16_t *)0x1fff7a22,
    revision,safety.voltage_mv,safety.power_valid,rd(0x4001203c),rd(0x40023804),
    rd(0x40023808),rd(0x40023820),rd(0x40020414),vgw_white_startup_now(&startup)};
  for (unsigned i=0;i<12;i++) put(out+4*i,fields[i]);
  return 48;
}
void vgw_loader_main(void) {
#ifdef VGW_PROBE_RECOVERY
  extern void vgw_usb_recovery_window(void);
  vgw_usb_recovery_window();
#endif
  const vgw_white_mmio *io=vgw_white_physical_mmio();
  vgw_white_identity identity;
  bool identified=vgw_white_identify(io,&identity);
  stage(0x10000000U|((uint32_t)identity.device_id<<16)|identity.flash_kib);
  if (!identified) vgw_white_physical_reset();
  stage(10);
  if (!vgw_white_quiesce(io)) vgw_white_physical_reset();
  stage(11);
  if (!vgw_white_startup_usb_only(&startup,io)) vgw_white_physical_reset();
  stage(2);
  if (!vgw_white_clock_usb_only(&clock,&startup)) vgw_white_physical_reset();
  stage(3);
  /* Exact historical White revision strap. No voltage-based revision guess.
   * PA13 is input while sampling, before USB client power-mode setup. */
  wr(0x40020000,rd(0x40020000)&~(3U<<26));
  wr(0x4002000c,(rd(0x4002000c)&~(3U<<26))|(2U<<26));
  for (volatile unsigned i=0;i<10000;i++) {}
  revision=(rd(0x40020010)>>13)&1U;
  for (unsigned i=0;i<16;i++) if (((rd(0x40020010)>>13)&1U)!=revision) vgw_white_physical_reset();
  wr(0x4002000c,rd(0x4002000c)&~(3U<<26));
  silent.clock=&clock; silent.ready=true; silent.config.hscan_mask=1;
  stage(4);
  if (!vgw_white_safety_init(&safety,&silent,0,revision ? 8862 : 3791)) vgw_white_physical_reset();
  stage(5);
  wr(0x2001c010,0);
  wr(0x2001c014,revision);
  wr(0x2001c018,rd(0xe0042000));
  if (!vgw_white_usb_init(&startup,false)) vgw_white_physical_reset();
  stage(6);
  uint32_t start=vgw_white_startup_now(&startup);
  vgw_status_led indicator;
  vgw_led_init(&indicator,start);
  vgw_led_set(&indicator,VGW_LED_PROBE,255,0,start);
  for (;;) {
    uint32_t now=vgw_white_startup_now(&startup);
    silent.elapsed_ms=now;
    uint64_t sampled; bool allowed;
    if (!vgw_white_safety_sample(&safety,now,&sampled,&allowed)) { stage(70); vgw_white_physical_reset(); }
    if (!vgw_white_usb_poll()) { stage(71); vgw_white_physical_reset(); }
    if ((uint32_t)(now-start)>=120000U) { stage(72); vgw_white_physical_reset(); }
    /* Same tested one-shot/gapped renderer as firmware, then blue idle.
     * The probe has no selected application slot and enables no CAN PHY. */
    vgw_white_led(io,vgw_led_sample(&indicator,now));
  }
}
