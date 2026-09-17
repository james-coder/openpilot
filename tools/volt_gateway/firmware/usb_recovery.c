#include "white_usb.h"
#include "white_clock.h"
#include "white_platform.h"
#include "usb_recovery_intent.h"
static vgw_white_startup recovery_startup;
static vgw_white_clock recovery_clock;
__attribute__((naked,noreturn)) static void enter_rom(uint32_t stack __attribute__((unused)),uint32_t entry __attribute__((unused))) {
  __asm__ volatile("movs r2,#0\nmsr control,r2\nisb\nmsr msp,r0\ndsb\nisb\ncpsie i\nbx r1");
}
/* Local USB cold-start recovery only; never reachable through CAN messages. */
void vgw_usb_recovery_window(void) {
  const vgw_white_mmio *io=vgw_white_physical_mmio();
  if (!vgw_white_startup_usb_only(&recovery_startup,io) ||
      !vgw_white_clock_usb_only(&recovery_clock,&recovery_startup)) vgw_white_physical_reset();
  bool requested=vgw_usb_recovery_take(io);
  bool direct=requested;
  /* A software request enters ROM directly after reset, before watchdog/CAN
   * startup. No ten-second enumeration race and no inherited running IWDG. */
  if (!requested && !vgw_white_usb_init(&recovery_startup,true)) vgw_white_physical_reset();
  uint32_t start=vgw_white_startup_now(&recovery_startup);
  for (unsigned budget=0;!requested && budget<50000000U;budget++) {
    if (!vgw_white_usb_poll()) break;
    if (vgw_white_usb_recovery_requested()) { requested=true; break; }
    if ((uint32_t)(vgw_white_startup_now(&recovery_startup)-start)>=10000U) break;
  }
  if (!direct) vgw_white_usb_stop();
  if (!requested) return;
  /* Exact historical White ROM entry, not a request-supplied address. The
   * read-only vector check is necessary but not hardware validation. */
  const uint32_t *rom=(const uint32_t *)0x1fff0000U;
  uint32_t sp=rom[0],pc=rom[1];
  bool address=(pc>=0x1fff0001U && pc<0x1fff7800U) || (pc>=0x1ff00001U && pc<0x1ff0f000U);
  if ((sp&7) || sp<=0x20000000U || sp>0x20020000U || !(pc&1U) || !address) vgw_white_physical_reset();
  if (!vgw_white_quiesce(io)) vgw_white_physical_reset();
  /* Return the clock tree to reset-like HSI before the ROM takes ownership. */
  io->write32(io->ctx,0x40023800U,io->read32(io->ctx,0x40023800U)|1U);
  io->write32(io->ctx,0x40023808U,0);
  unsigned timeout=0;
  while (io->read32(io->ctx,0x40023808U)&12U) if (++timeout==100000U) vgw_white_physical_reset();
  io->write32(io->ctx,0x40023800U,0x00000083U);
  io->write32(io->ctx,0x40000000U,0);
  io->write32(io->ctx,0xe000e010U,0);
  for (unsigned i=0;i<8;i++) {
    io->write32(io->ctx,0xe000e180U+4*i,0xffffffffU);
    io->write32(io->ctx,0xe000e280U+4*i,0xffffffffU);
  }
  io->write32(io->ctx,0xe000ed08U,0x1fff0000U);
  enter_rom(sp,pc);
}
