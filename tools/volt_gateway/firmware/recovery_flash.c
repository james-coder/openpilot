#include "recovery_flash.h"
#define RAM __attribute__((section(".ramfunc.vgw_recovery_flash"), noinline))
bool vgw_recovery_flash_init(vgw_recovery_flash *s,vgw_white_runtime *runtime,vgw_recovery_service *recovery,
  bool (*sample)(void *,uint64_t,uint64_t *,bool *),void (*receive)(void *,const vgw_frame *),void *context) {
  if (!s || !runtime || !runtime->ready || !recovery || !recovery->ready || !sample || !receive) return false;
#if defined(__arm__) || defined(__thumb__)
  uintptr_t functions[]={(uintptr_t)sample,(uintptr_t)receive};
  for (unsigned i=0;i<2;i++) if (!(functions[i]&1U) || functions[i]<0x20000001U || functions[i]>=0x20020000U) return false;
  if ((uintptr_t)s<0x20000000U || (uintptr_t)s>0x20020000U-sizeof(*s) ||
      (uintptr_t)runtime<0x20000000U || (uintptr_t)runtime>0x20020000U-sizeof(*runtime) ||
      (uintptr_t)recovery<0x20000000U || (uintptr_t)recovery>0x20020000U-sizeof(*recovery)) return false;
#endif
  *s=(vgw_recovery_flash){.runtime=runtime,.recovery=recovery,.sample=sample,.receive=receive,.context=context,.enabled=true};
  return true;
}
static RAM void fail(vgw_recovery_flash *s) {
  s->failed=true; s->enabled=false;
  vgw_white_watchdog_fail(&s->runtime->startup.watchdog);
  vgw_white_can_stop(&s->runtime->can);
}
bool RAM vgw_recovery_flash_permit(void *ctx) {
  vgw_recovery_flash *s=ctx;
  if (!s || !s->enabled || s->failed) return false;
  vgw_white_runtime *r=s->runtime; vgw_recovery_service *u=s->recovery;
  uint32_t delta=vgw_white_startup_now(&r->startup)-r->can.previous_ms;
  uint64_t now=r->can.elapsed_ms+delta,sampled=0; bool allowed=false;
  if (!r->ready || r->startup.watchdog.failed || !r->can.ready || r->can.failed || r->can.pending ||
      r->can.tx_inhibited || delta>250U || !u->ready || !u->active || now>=u->expires ||
      !u->authority.granted || u->authority.closed || u->authority.phase!=VGW_PROGRAM ||
      now>=u->authority.grant_until || now<u->previous ||
      !s->sample(s->context,now,&sampled,&allowed) || !allowed || sampled>now || now-sampled>30U) return false;
  return true;
}
static RAM void receive(void *ctx,const vgw_frame *f) {
  vgw_recovery_flash *s=ctx;
  if (s->received!=UINT32_MAX) s->received++;
  /* Keep the current authenticated request buffer immutable during a flash
   * command. Drop new command ingress; the host retries after the operation.
   * Continue the trusted passive safety decoder on all observed frames. */
  if (s->runtime->can.config.backhaul_controller && f->bus==s->runtime->can.config.backhaul_controller-1U &&
      !(f->flags&VGW_EXTENDED) && f->address==s->runtime->recovery.rx.request_id && s->dropped_commands!=UINT32_MAX)
    s->dropped_commands++;
  s->receive(s->context,f);
}
void RAM vgw_recovery_flash_service(void *ctx) {
  vgw_recovery_flash *s=ctx;
  if (!s || !s->runtime) return;
  if (!s->enabled || s->failed) { fail(s); return; }
  vgw_white_watchdog *w=&s->runtime->startup.watchdog;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_SCHEDULER);
  if (!vgw_white_can_poll(&s->runtime->can,receive,s)) { fail(s); return; }
  vgw_white_watchdog_progress(w,VGW_PROGRESS_RX);
  /* Ingress above is actually drained/discarded, not interpreted recursively.
   * A flash deadline is independently enforced by white_flash.wait_idle. */
  vgw_white_watchdog_progress(w,VGW_PROGRESS_PROTOCOL);
  if (!vgw_recovery_flash_permit(s)) { fail(s); return; }
  vgw_white_watchdog_progress(w,VGW_PROGRESS_HEALTH);
  /* white_platform.service performs the final independent watchdog check/feed.
   * No TX, crypto, flash reads or application/observer callbacks here. */
}
