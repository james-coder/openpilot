#include "recovery_link.h"
static uint32_t inc(uint32_t n) { return n==UINT32_MAX ? n : n+1; }
static void clear_reply(vgw_recovery_link *s) {
  for (unsigned i=0;i<VGW_RECOVERY_PDU_MAX;i++) s->reply[i]=0;
  s->replying=s->waiting=s->unlimited=false; s->length=s->offset=0;
  s->sequence=1; s->remaining=s->waits=0;
}
void vgw_recovery_link_close(vgw_recovery_link *s) {
  if (!s) return;
  clear_reply(s); vgw_recovery_rx_clear(&s->rx);
  s->flow_pending=s->ready=false;
}
bool vgw_recovery_link_init(vgw_recovery_link *s,uint16_t id) {
  if (!s) return false;
  *s=(vgw_recovery_link){0};
  s->ready=vgw_recovery_rx_init(&s->rx,id); return s->ready;
}
static bool clock_ok(vgw_recovery_link *s,uint32_t now) {
  if (!s || !s->ready) return false;
  if ((uint32_t)(now-s->previous)>0x7fffffffU) { vgw_recovery_link_close(s); return false; }
  s->previous=now;
  return true;
}
static vgw_rx_result reject(vgw_recovery_link *s) {
  s->rejects=inc(s->rejects);
  clear_reply(s); vgw_recovery_rx_clear(&s->rx); s->flow_pending=false;
  return VGW_RX_REJECT;
}
static bool expire(vgw_recovery_link *s,uint32_t now) {
  bool expired=vgw_recovery_rx_expire(&s->rx,now);
  if (s->replying && ((uint32_t)(now-s->started)>10000U || (uint32_t)(now-s->last)>1000U)) {
    clear_reply(s); expired=true;
  }
  if (expired) { s->timeouts=inc(s->timeouts); s->flow_pending=false; }
  return expired;
}
vgw_rx_result vgw_recovery_link_feed(vgw_recovery_link *s,uint32_t id,bool ext,bool rtr,
                                    const uint8_t *frame,size_t dlc,uint32_t now) {
  if (!clock_ok(s,now)) return VGW_RX_REJECT;
  if (id!=s->rx.request_id || ext) return VGW_RX_IGNORE;
  if (expire(s,now)) return VGW_RX_REJECT;
  if (rtr || !frame || dlc!=8) return reject(s);
  if ((frame[0]>>4)==3) {
    if (!s->replying || !s->waiting) return VGW_RX_IGNORE;
    if (frame[3]||frame[4]||frame[5]||frame[6]||frame[7]) return reject(s);
    if (frame[0]==0x31) { /* WAIT: bounded, never extends total deadline */
      if (++s->waits>3) return reject(s);
      s->last=now; return VGW_RX_MORE;
    }
    if (frame[0]!=0x30 || (frame[2]>0x7f && (frame[2]<0xf1 || frame[2]>0xf9))) return reject(s);
    s->remaining=frame[1]; s->unlimited=frame[1]==0;
    s->separation_ms=(frame[2]>=0xf1 || frame[2]<10) ? 10 : frame[2];
    s->waiting=false; s->last=now;
    return VGW_RX_MORE;
  }
  if (s->replying || s->rx.complete) return VGW_RX_IGNORE;
  if (s->flow_pending) return reject(s); /* peer exceeded advertised block grant */
  vgw_rx_result result=vgw_recovery_rx_feed(&s->rx,id,false,false,frame,dlc,now);
  if (result==VGW_RX_FLOW) s->flow_pending=true;
  if (result==VGW_RX_REJECT) s->rejects=inc(s->rejects);
  return result;
}
bool vgw_recovery_link_reply(vgw_recovery_link *s,const uint8_t *data,size_t n,uint32_t now) {
  if (!clock_ok(s,now) || !data || !n || n>VGW_RECOVERY_PDU_MAX || s->replying || !s->rx.complete) return false;
  clear_reply(s);
  for (unsigned i=0;i<n;i++) s->reply[i]=data[i];
  s->length=(uint16_t)n; s->replying=true; s->started=s->last=now;
  vgw_recovery_rx_clear(&s->rx); s->flow_pending=false;
  return true;
}
bool vgw_recovery_link_poll(vgw_recovery_link *s,uint32_t now,vgw_link_send send,void *ctx) {
  if (!clock_ok(s,now) || !send) return false;
  if (expire(s,now)) return false;
  uint8_t frame[8]={0};
  if (s->flow_pending) {
    frame[0]=0x30; frame[1]=8; frame[2]=10;
    if (!send(ctx,frame)) return false;
    s->flow_pending=false; return true;
  }
  if (!s->replying || s->waiting) return false;
  unsigned n,head;
  if (s->length<=7) { frame[0]=(uint8_t)s->length; n=s->length; head=1; }
  else if (!s->offset) {
    frame[0]=(uint8_t)(0x10|(s->length>>8)); frame[1]=(uint8_t)s->length; n=6; head=2;
  } else {
    if ((uint32_t)(now-s->last)<s->separation_ms) return false;
    frame[0]=(uint8_t)(0x20|s->sequence); n=s->length-s->offset;
    if (n>7) n=7;
    head=1;
  }
  for (unsigned i=0;i<n;i++) frame[head+i]=s->reply[s->offset+i];
  if (!send(ctx,frame)) return false;
  bool first=s->offset==0;
  s->offset+=(uint16_t)n; s->last=now;
  if (s->offset==s->length) { s->completed=inc(s->completed); clear_reply(s); }
  else if (first) s->waiting=true;
  else {
    s->sequence=(s->sequence+1U)&15U;
    if (!s->unlimited && --s->remaining==0) s->waiting=true;
  }
  return true;
}
