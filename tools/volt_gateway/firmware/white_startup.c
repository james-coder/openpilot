#include "white_startup.h"
#define CR 0x40023800U
#define CFGR 0x40023808U
#define APB1RSTR 0x40023820U
#define APB1ENR 0x40023840U
#define TIM2 0x40000000U
static void change(const vgw_white_mmio *io, uint32_t address, uint32_t mask, uint32_t set) {
  io->write32(io->ctx,address,(io->read32(io->ctx,address)&~mask)|set);
}
static bool wait(const vgw_white_mmio *io, uint32_t address, uint32_t mask, uint32_t expected) {
  for (unsigned i=0;i<100000U;i++) if ((io->read32(io->ctx,address)&mask)==expected) return true;
  return false;
}
static bool clock_timer(const vgw_white_mmio *io) {
  change(io,CR,0,1); /* HSI enable; don't disable the inherited source early. */
  if (!wait(io,CR,2,2)) return false;
  change(io,CFGR,3,0);
  if (!wait(io,CFGR,12,0)) return false;
  change(io,CFGR,0xfcf0U,0); /* HCLK/PCLK1/PCLK2 divide by one at 16MHz. */
  if (io->read32(io->ctx,CFGR)&0xfcfcU) return false;
  change(io,APB1ENR,0,1);
  if (!(io->read32(io->ctx,APB1ENR)&1)) return false;
  change(io,APB1RSTR,0,1);
  if (!(io->read32(io->ctx,APB1RSTR)&1)) return false;
  change(io,APB1RSTR,1,0);
  if (io->read32(io->ctx,APB1RSTR)&1) return false;
  io->write32(io->ctx,TIM2,0); /* CR1 */
  io->write32(io->ctx,TIM2+12,0); /* no IRQ/DMA dependency */
  io->write32(io->ctx,TIM2+40,15999); /* PSC: 1kHz => full-width milliseconds */
  io->write32(io->ctx,TIM2+44,0xffffffffU);
  io->write32(io->ctx,TIM2+20,1); /* UG loads prescaler */
  io->write32(io->ctx,TIM2+16,0); /* clear UIF */
  io->write32(io->ctx,TIM2+36,0);
  io->write32(io->ctx,TIM2,1);
  return io->read32(io->ctx,TIM2)==1 && io->read32(io->ctx,TIM2+12)==0 &&
    io->read32(io->ctx,TIM2+40)==15999 && io->read32(io->ctx,TIM2+44)==0xffffffffU;
}
bool vgw_white_startup_init(vgw_white_startup *s, const vgw_white_mmio *io) {
  if (!s) return false;
  *s=(vgw_white_startup){0};
  if (!vgw_white_quiesce(io) || !vgw_white_identify(io,&s->identity)) return false;
  if (!vgw_white_watchdog_start(&s->watchdog,io,0)) return false;
  if (!clock_timer(io)) { vgw_white_watchdog_fail(&s->watchdog); return false; }
  s->ready=true;
  return true;
}
bool vgw_white_startup_usb_only(vgw_white_startup *s,const vgw_white_mmio *io) {
  if (!s || !io) return false;
  *s=(vgw_white_startup){0};
  if (!vgw_white_quiesce(io) || !vgw_white_identify(io,&s->identity) || !clock_timer(io)) return false;
  s->watchdog.io=*io; s->ready=true;
  return true;
}
__attribute__((section(".ramfunc.vgw_startup"), noinline))
uint32_t vgw_white_startup_now(const vgw_white_startup *s) {
  return s && s->ready ? s->watchdog.io.read32(s->watchdog.io.ctx,TIM2+36) : 0;
}
