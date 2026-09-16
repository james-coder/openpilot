#include "white_runtime.h"
size_t vgw_white_runtime_size(void) { return sizeof(vgw_white_runtime); }
void vgw_white_runtime_stop(vgw_white_runtime *s) {
  if (!s) return;
  s->ready=false;
  vgw_recovery_link_close(&s->recovery);
  vgw_white_watchdog_fail(&s->startup.watchdog);
  if (s->can.clock) vgw_white_can_stop(&s->can);
  else if (s->startup.watchdog.io.write32) (void)vgw_white_quiesce(&s->startup.watchdog.io);
  for (unsigned i=0;i<32;i++) s->boot_nonce[i]=0;
}
bool vgw_white_runtime_init(vgw_white_runtime *s,const vgw_white_mmio *io,const vgw_white_can_config *config,uint16_t request_id) {
  if (!s || !io || !config || request_id>0x7ffU ||
      (config->backhaul_controller && request_id==config->response_id)) return false;
  uint8_t *bytes=(uint8_t *)s;
  for (unsigned i=0;i<sizeof(*s);i++) bytes[i]=0;
  vgw_observer_init(&s->observer);
  if (!vgw_white_startup_init(&s->startup,io) || !vgw_white_clock_init(&s->clock,&s->startup) ||
      !vgw_white_rng_init(&s->rng,&s->clock) || !vgw_white_rng_nonce(&s->rng,s->boot_nonce) ||
      !vgw_recovery_link_init(&s->recovery,request_id) || !vgw_white_can_init(&s->can,&s->clock,config)) {
    vgw_white_runtime_stop(s); return false;
  }
  s->ready=true; return true;
}
static void receive(void *ctx,const vgw_frame *f) {
  vgw_white_runtime *s=ctx;
  (void)vgw_observer_feed(&s->observer,f);
  if (s->listener) s->listener(s->listener_context,f);
  if (!s->local_control && s->can.config.backhaul_controller && f->bus==s->can.config.backhaul_controller-1U)
    (void)vgw_recovery_link_feed(&s->recovery,f->address,(f->flags&VGW_EXTENDED)!=0,
      (f->flags&VGW_RTR)!=0,f->data,f->dlc,(uint32_t)s->can.elapsed_ms);
}
static bool send(void *ctx,const uint8_t data[8]) {
  vgw_white_runtime *s=ctx; return vgw_white_can_response(&s->can,data);
}
bool vgw_white_runtime_step(vgw_white_runtime *s,vgw_runtime_protocol protocol,void *ctx) {
  if (!s || !s->ready) return false;
  if (!protocol) goto fail;
  vgw_white_watchdog *w=&s->startup.watchdog;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_SCHEDULER);
  if (!vgw_white_can_poll(&s->can,receive,s)) goto fail;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_RX);
  if (!protocol(ctx,s)) goto fail;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_PROTOCOL);
  if (!vgw_white_rng_health(&s->rng) || !s->recovery.ready || !vgw_white_clock_valid(&s->clock)) goto fail;
  vgw_white_watchdog_progress(w,VGW_PROGRESS_HEALTH);
  if (!vgw_white_watchdog_service(w,vgw_white_startup_now(&s->startup))) goto fail;
  /* No transmit if protocol work blocked long enough to stale CAN health. */
  if (!s->local_control) (void)vgw_recovery_link_poll(&s->recovery,(uint32_t)s->can.elapsed_ms,send,s);
  return true;
fail:
  vgw_white_runtime_stop(s); return false;
}
