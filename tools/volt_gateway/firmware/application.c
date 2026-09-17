#include "application.h"
#include "recovery_runtime.h"
size_t vgw_application_size(void) { return sizeof(vgw_application); }
void vgw_application_indication(vgw_application *s,const vgw_status_led *led,uint8_t rgb) {
  if (!s || !led) return;
  s->indication[0]=led->state; s->indication[1]=led->slot; s->indication[2]=led->error;
  s->indication[3]=rgb; s->indication[4]=led->intro_active;
  s->indication_valid=true;
}
bool vgw_application_indicator_snapshot(const vgw_application *s,uint8_t out[7]) {
  if (!s || !out || !s->indication_valid || !s->runtime || !s->session) return false;
  out[0]=1;
  for (unsigned i=0;i<5;i++) out[1+i]=s->indication[i];
  out[6]=!s->runtime->can.config.backhaul_controller ? 0 : s->runtime->local_control ? 1 :
    s->session->active ? 3 : s->can_peer_seen ? 4 : 2;
  return true;
}
static uint16_t get16(const uint8_t *p) { return (uint16_t)(((uint16_t)p[0]<<8)|p[1]); }
static uint32_t get32(const uint8_t *p) { return ((uint32_t)get16(p)<<16)|get16(p+2); }
static void put16(uint8_t *p,uint16_t v) { p[0]=(uint8_t)(v>>8); p[1]=(uint8_t)v; }
static void put32(uint8_t *p,uint32_t v) { put16(p,(uint16_t)(v>>16)); put16(p+2,(uint16_t)v); }
static void put64(uint8_t *p,uint64_t v) { put32(p,(uint32_t)(v>>32)); put32(p+4,(uint32_t)v); }
static void inc(uint32_t *p) { if (*p!=UINT32_MAX) ++*p; }
static bool bus(vgw_application *s,uint8_t b) {
  /* CAN driver gives SWCAN the stable logical index 3, independently of
   * whether CAN2 or CAN3 is muxed to its transceiver. */
  return b==3 || (b<3 && (s->runtime->can.config.hscan_mask&(1U<<b)));
}
static void frame(uint8_t *p,const vgw_frame *f) {
  put64(p,f->timestamp_us); put32(p+8,f->sequence); put32(p+12,f->address);
  p[16]=f->bus; p[17]=f->flags; p[18]=f->dlc;
  for (unsigned i=0;i<8;i++) p[19+i]=f->data[i];
}
void vgw_application_reset(void *ctx) {
  vgw_application *s=ctx;
  if (!s || !s->runtime) return;
  vgw_observer *o=&s->runtime->observer;
  for (unsigned i=0;i<4;i++) (void)vgw_observe_stop(o,(uint8_t)i);
  vgw_capture_stop(o); vgw_clear_subscriptions(o);
  /* Handles can be reused only after the entire authenticated session ends. */
  o->issued_handles=0;
  if (s->pending) inc(&s->telemetry_drops);
  s->pending=false; s->fragment=0; s->lease_until=0;
}
bool vgw_application_init(vgw_application *s,vgw_white_runtime *r,vgw_recovery_service *session) {
  if (!s || !r || !r->ready || !session || !session->ready) return false;
  *s=(vgw_application){.runtime=r,.session=session};
  return vgw_recovery_service_application(session,vgw_application_command,vgw_application_reset,s);
}
size_t vgw_application_command(void *ctx,uint8_t op,const uint8_t *p,size_t n,uint64_t now,uint8_t out[128]) {
  vgw_application *s=ctx;
  if (!s || !s->runtime || !s->runtime->ready || !out || (!p && n) || now>=(UINT64_C(1)<<63)/1000U) return 0;
  vgw_observer *o=&s->runtime->observer;
  size_t length=1; bool ok=false;
  /* Every valid command refreshes telemetry liveness; firmware update requests
   * deliberately do not. No unauthenticated request can start observation. */
  switch (op) {
    case 1: /* INFO: capabilities, firmware build, fixed mapping, protocol */
      if (!n) {
        ok=true; out[1]=1; out[2]=1; put32(out+3,0x0000003fU);
        for (unsigned i=0;i<32;i++) out[7+i]=s->session->provision.build[i];
        out[39]=s->runtime->can.config.swcan_controller;
        out[40]=s->runtime->can.config.hscan_mask;
        out[41]=s->runtime->can.config.backhaul_controller;
        put16(out+42,VGW_IDS); put16(out+44,VGW_CAPTURE); out[46]=VGW_SUBSCRIPTIONS;
        length=47;
      } break;
    case 2: /* PING / uptime / reset flags / observer and telemetry counters */
      if (!n) {
        ok=true; put64(out+1,now); put32(out+9,s->runtime->startup.watchdog.reset_flags);
        put32(out+13,o->id_drops); put32(out+17,o->capture_drops); put32(out+21,o->invalid);
        put32(out+25,s->telemetry_drops); put32(out+29,s->telemetry_completed); length=33;
      } break;
    case 3: /* BUS_STATUS: raw conservative window accounting, not a promise of spare bandwidth */
      if (n==1 && bus(s,p[0])) {
        unsigned controller=p[0]==3 ? s->runtime->can.config.swcan_controller-1U : p[0];
        const vgw_white_can_stats *v=&s->runtime->can.stats[controller];
        uint32_t values[]={v->received,v->malformed,v->overflow,v->transmitted,v->arbitration_lost,
          v->tx_errors,v->last_esr,v->peak_permille,v->window_start[0],v->window_bits[0],
          v->window_start[1],v->window_bits[1],v->window_start[2],v->window_bits[2]};
        for (unsigned i=0;i<14;i++) put32(out+1+4*i,values[i]);
        ok=true; length=57;
      } break;
    case 4: if (n==1 && bus(s,p[0])) ok=vgw_clear_ids(o,p[0]); break;
    case 5: if (n==3 && bus(s,p[0])) ok=vgw_observe_start(o,p[0],get16(p+1),now*1000U); break;
    case 6: if (n==1 && bus(s,p[0])) ok=vgw_observe_stop(o,p[0]); break;
    case 7: /* ID table page: two records, cursor is a bounded table index */
      if (n==3 && bus(s,p[0]) && get16(p+1)<=VGW_IDS) {
        unsigned cursor=get16(p+1),count=0; length=4;
        while (cursor<VGW_IDS && count<2) {
          vgw_id_stat v;
          if (vgw_get_id(o,(uint16_t)cursor,&v) && v.last.bus==p[0]) {
            frame(out+length,&v.last); put64(out+length+27,v.first_us); put32(out+length+35,v.count);
            put16(out+length+39,v.dlc_mask); out[length+41]=v.changed_mask;
            for (unsigned i=0;i<8;i++) put16(out+length+42+2*i,v.changes[i]);
            length+=58; count++;
          }
          cursor++;
        }
        put16(out+1,(uint16_t)cursor); out[3]=(uint8_t)count; ok=true;
      } break;
    case 8: /* SUBSCRIBE: handle,bus,flags,address32,max_hz16 */
      if (n==9 && bus(s,p[1])) ok=vgw_subscribe(o,p[0],p[1],get32(p+3),p[2],get16(p+7));
      break;
    case 9: if (n==1 && p[0]<VGW_SUBSCRIPTIONS) { vgw_unsubscribe(o,p[0]); ok=true; } break;
    case 10: if (!n) { vgw_clear_subscriptions(o); s->pending=false; ok=true; } break;
    case 11: /* CAPTURE: logical bus mask, seconds16; bit3 is SWCAN */
      if (n==3 && p[0] && !(p[0]&~15U)) {
        bool allowed=true;
        for (uint8_t b=0;b<4;b++) if ((p[0]&(1U<<b)) && !bus(s,b)) allowed=false;
        if (allowed) ok=vgw_capture_start(o,p[0],get16(p+1),now*1000U);
      } break;
    case 12: if (!n) { vgw_capture_stop(o); ok=true; } break;
    case 13: /* Four capture records per page; no unbounded download */
      if (n==2 && get16(p)<=o->capture_count) {
        uint16_t index=get16(p); unsigned count=0; length=4;
        vgw_frame f;
        while (count<4 && vgw_get_capture(o,index,&f)) { frame(out+length,&f); length+=27; index++; count++; }
        put16(out+1,index); out[3]=(uint8_t)count; ok=true;
      } break;
    case 14: /* Write policy is intentionally empty. No generic TX rule engine. */
      if (!n) { ok=true; out[1]=0; length=2; } break;
    case 15: /* Versioned snapshot of the actual renderer, not inferred health.
              * Link: disabled/local USB/awaiting/authenticated/stale. */
      if (!n && vgw_application_indicator_snapshot(s,out+1)) { ok=true; length=8; } break;
    case 16: /* RX staging diagnostics; separate from historical bus counters. */
      if (n==1 && bus(s,p[0])) {
        unsigned c=p[0]==3 ? s->runtime->can.config.swcan_controller-1U : p[0];
        const vgw_white_can_stats *v=&s->runtime->can.stats[c];
        put32(out+1,v->software_drops); put32(out+5,v->irq_calls);
        put32(out+9,v->queue_peak); put32(out+13,v->max_queue_age_ms);
        ok=true; length=17;
      } break;
    default: break;
  }
  out[0]=ok ? 0 : 1;
  if (ok) s->lease_until=now+10000U;
  return length;
}
bool vgw_application_step(void *ctx,vgw_white_runtime *runtime) {
  vgw_application *s=ctx;
  if (!s || s->runtime!=runtime || !vgw_recovery_runtime_step(s->session,runtime)) return false;
  if (!runtime->local_control && runtime->can.config.backhaul_controller && s->session->active) s->can_peer_seen=true;
  if (!s->session->active || runtime->can.elapsed_ms>=s->lease_until ||
      s->session->update.state==VGW_UPDATE_RECEIVING) {
    if (s->pending) inc(&s->telemetry_drops);
    s->pending=false;
    vgw_clear_subscriptions(&runtime->observer);
  }
  return true;
}
void vgw_application_telemetry(vgw_application *s) {
  if (!s || !s->runtime || !s->runtime->ready || !s->session || !s->session->active) return;
  vgw_white_runtime *r=s->runtime;
  if (r->can.elapsed_ms>=s->lease_until || s->session->update.state==VGW_UPDATE_RECEIVING) return;
  /* ISO-TP control wins. A partially emitted telemetry record is discarded when
   * control arrives; no stale backlog after a long transfer. */
  if (r->recovery.rx.complete || r->recovery.rx.active || r->recovery.replying || r->recovery.flow_pending) {
    if (s->pending) { inc(&s->telemetry_drops); s->pending=false; }
    return;
  }
  if (s->pending && r->can.elapsed_ms>=s->record_until) { inc(&s->telemetry_drops); s->pending=false; }
  if (!s->pending) {
    vgw_frame f; uint8_t handle;
    if (!vgw_next_observation(&r->observer,r->can.elapsed_ms*1000U,100000U,&f,&handle)) return;
    put32(s->record,(uint32_t)f.timestamp_us); put32(s->record+4,f.address);
    for (unsigned i=0;i<8;i++) s->record[8+i]=f.data[i];
    s->record[16]=(uint8_t)((f.bus<<6)|(f.flags<<4)|f.dlc); s->record[17]=handle;
    s->sequence=(uint16_t)((s->sequence+1)&4095U); s->fragment=0; s->pending=true;
    s->record_until=r->can.elapsed_ms+100U;
  }
  uint8_t data[8];
  data[0]=(uint8_t)(((10U+s->fragment)<<4)|(s->sequence>>8)); data[1]=(uint8_t)s->sequence;
  for (unsigned i=0;i<6;i++) data[2+i]=s->record[6*s->fragment+i];
  bool sent=r->local_control ? (s->local_send && s->local_send(s->local_context,data)) : vgw_white_can_response(&r->can,data);
  if (sent && ++s->fragment==3) {
    s->pending=false; inc(&s->telemetry_completed);
  }
}
