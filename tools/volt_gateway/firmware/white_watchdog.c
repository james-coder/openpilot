#include "white_watchdog.h"
#include <stddef.h>
#define RAM __attribute__((section(".ramfunc.vgw_watchdog"), noinline))
#define CSR 0x40023874U
#define KR 0x40003000U
#define PR 0x40003004U
#define RLR 0x40003008U
#define SR 0x4000300cU
#define POLLS 100000U

static bool wait_bits(const vgw_white_mmio *io, uint32_t addr, uint32_t mask, uint32_t expected) {
  for (unsigned i=0;i<POLLS;i++) if ((io->read32(io->ctx,addr)&mask)==expected) return true;
  return false;
}
bool vgw_white_watchdog_start(vgw_white_watchdog *s, const vgw_white_mmio *io, uint32_t now) {
  if (!s || !io || !io->read32 || !io->write32) return false;
  *s=(vgw_white_watchdog){.io=*io,.epoch_ms=now,.failed=true};
  s->reset_flags=io->read32(io->ctx,CSR); /* preserve before any reset-flag clear */
  io->write32(io->ctx,CSR,(s->reset_flags & ~(1U<<24))|1U); /* LSION, not RMVF */
  if (!wait_bits(io,CSR,2U,2U)) return false;
  io->write32(io->ctx,KR,0xccccU); /* Start first: configuration stalls still reset. */
  s->started=true;
  io->write32(io->ctx,KR,0x5555U);
  /* ROM/handoff may leave an LSI-domain update in flight. RM0430 requires
   * PVU/RVU clear before changing PR/RLR, not only after the writes. */
  if (!wait_bits(io,SR,3U,0)) return false;
  io->write32(io->ctx,PR,3U); /* /32 */
  io->write32(io->ctx,RLR,1999U);
  /* Bound synchronization of both the status and actual readback. A single
   * early SR==0 sample need not establish the requested values are visible. */
  bool configured=false;
  for (unsigned i=0;i<POLLS;i++) {
    if (!(io->read32(io->ctx,SR)&3U) && io->read32(io->ctx,PR)==3U &&
        io->read32(io->ctx,RLR)==1999U) { configured=true; break; }
  }
  if (!configured) return false;
  io->write32(io->ctx,KR,0xaaaaU);
  s->failed=false;
  return true;
}
RAM void vgw_white_watchdog_fail(vgw_white_watchdog *s) {
  if (s) { s->failed=true; s->seen=0; }
}
RAM void vgw_white_watchdog_progress(vgw_white_watchdog *s, uint32_t completed) {
  if (!s || s->failed || !s->started) return;
  if (!completed || (completed & ~VGW_PROGRESS_ALL)) { vgw_white_watchdog_fail(s); return; }
  s->seen |= completed;
}
RAM bool vgw_white_watchdog_service(vgw_white_watchdog *s, uint32_t now) {
  if (!s || !s->started || s->failed) return false;
  uint32_t elapsed=now-s->epoch_ms;
  if (elapsed>250U) { vgw_white_watchdog_fail(s); return false; }
  if (elapsed>=50U && s->seen==VGW_PROGRESS_ALL) {
    s->io.write32(s->io.ctx,KR,0xaaaaU);
    s->epoch_ms=now; s->seen=0;
  }
  return true;
}
bool vgw_white_watchdog_was_reset(const vgw_white_watchdog *s) {
  return s && (s->reset_flags & (1U<<29));
}
