#include "white_clock.h"
#define CR 0x40023800U
#define PLL 0x40023804U
#define CFGR 0x40023808U
#define PLL_VALUE 0x24401808U
#define PROFILE 0x9400U
#define RAM __attribute__((section(".ramfunc.vgw_clock"), noinline))
static RAM uint32_t rd(const vgw_white_mmio *io,uint32_t a) { return io->read32(io->ctx,a); }
static void wr(const vgw_white_mmio *io,uint32_t a,uint32_t v) { io->write32(io->ctx,a,v); }
static void change(const vgw_white_mmio *io,uint32_t a,uint32_t mask,uint32_t v) { wr(io,a,(rd(io,a)&~mask)|v); }
static bool wait(const vgw_white_mmio *io,uint32_t a,uint32_t mask,uint32_t v) {
  uint32_t start=rd(io,0x40000024U);
  for (unsigned i=0;i<100000U;i++) {
    if ((rd(io,a)&mask)==v) return true;
    if ((uint32_t)(rd(io,0x40000024U)-start)>50U) return false;
  }
  return false;
}
RAM bool vgw_white_clock_valid(const vgw_white_clock *c) {
  if (!c || !c->ready || !c->startup || !c->startup->ready || c->startup->watchdog.failed) return false;
  const vgw_white_mmio *io=&c->startup->watchdog.io;
  return (rd(io,CR)&0x03070000U)==0x03030000U && rd(io,PLL)==PLL_VALUE &&
    (rd(io,CFGR)&0xfcffU)==(PROFILE|10U) && !(rd(io,0x4002388cU)&(1U<<24)) &&
    !(rd(io,0x40023894U)&(1U<<27)) && (rd(io,0x40023c00U)&15U)==5U &&
    (rd(io,0x40007000U)&0xc000U)==0xc000U && (rd(io,0x40007004U)&0x4000U) &&
    rd(io,0x40000028U)==47999U && rd(io,0x40000000U)==1U;
}
static bool configure(vgw_white_clock *c,vgw_white_startup *s,bool usb_only) {
  if (!c) return false;
  *c=(vgw_white_clock){.startup=s};
  if (!s || !s->ready || s->watchdog.started==usb_only || s->watchdog.failed) return false;
  const vgw_white_mmio *io=&s->watchdog.io;
  /* Require the initial HSI profile with all three CAN controllers reset. */
  if ((rd(io,CFGR)&15U) || (rd(io,CR)&(1U<<18)) ||
      (rd(io,0x40023820U)&(7U<<25))!=(7U<<25)) goto fail;
  change(io,CR,1U<<24,0);
  if (!wait(io,CR,1U<<25,0)) goto fail;
  change(io,0x40023840U,0,1U<<28);
  if (!(rd(io,0x40023840U)&(1U<<28))) goto fail;
  change(io,0x40007000U,0xc000U,0xc000U);
  if ((rd(io,0x40007000U)&0xc000U)!=0xc000U) goto fail;
  change(io,CR,0,1U<<16);
  if (!wait(io,CR,1U<<17,1U<<17)) goto fail;
  wr(io,PLL,PLL_VALUE);
  if (rd(io,PLL)!=PLL_VALUE) goto fail;
  /* Raise flash latency before increasing HCLK. Retain other ACR settings. */
  change(io,0x40023c00U,15U,5U);
  if ((rd(io,0x40023c00U)&15U)!=5U) goto fail;
  change(io,CFGR,0xfcf0U,PROFILE);
  change(io,0x4002388cU,1U<<24,0); /* TIMPRE=0: APB1 timer clock x2 */
  change(io,0x40023894U,1U<<27,0); /* CK48 from main PLLQ */
  if ((rd(io,CFGR)&0xfcf0U)!=PROFILE || (rd(io,0x4002388cU)&(1U<<24)) ||
      (rd(io,0x40023894U)&(1U<<27))) goto fail;
  change(io,CR,0,1U<<24);
  if (!wait(io,CR,1U<<25,1U<<25) || !wait(io,0x40007004U,0x4000U,0x4000U)) goto fail;
  change(io,CFGR,3U,2U);
  if (!wait(io,CFGR,12U,8U)) goto fail;
  uint32_t counter=rd(io,0x40000024U);
  wr(io,0x40000000U,0);
  wr(io,0x40000028U,47999U);
  wr(io,0x40000014U,1);
  wr(io,0x40000010U,0);
  wr(io,0x40000024U,counter);
  wr(io,0x40000000U,1);
  c->ready=true;
  if (vgw_white_clock_valid(c)) return true;
fail:
  c->ready=false; vgw_white_watchdog_fail(&s->watchdog); s->ready=false;
  (void)vgw_white_quiesce(io);
  return false;
}
bool vgw_white_clock_init(vgw_white_clock *c,vgw_white_startup *s) { return configure(c,s,false); }
bool vgw_white_clock_usb_only(vgw_white_clock *c,vgw_white_startup *s) { return configure(c,s,true); }
