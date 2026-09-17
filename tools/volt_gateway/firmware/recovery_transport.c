#include "recovery_transport.h"
void vgw_recovery_rx_clear(vgw_recovery_rx *s) {
  if (!s) return;
  for (unsigned i=0;i<VGW_RECOVERY_PDU_MAX;i++) s->data[i]=0;
  s->length=s->offset=0; s->active=s->complete=false; s->expected=1; s->block=0;
}
bool vgw_recovery_rx_init(vgw_recovery_rx *s, uint16_t id) {
  if (!s) return false;
  *s=(vgw_recovery_rx){0};
  if (id>0x7ffU) return false;
  s->request_id=id; s->configured=true; s->expected=1;
  return true;
}
bool vgw_recovery_rx_expire(vgw_recovery_rx *s, uint32_t now) {
  if (!s || !s->active) return false;
  if ((uint32_t)(now-s->last)<=1000U && (uint32_t)(now-s->started)<=10000U) return false;
  vgw_recovery_rx_clear(s); return true;
}
static vgw_rx_result reject(vgw_recovery_rx *s) { vgw_recovery_rx_clear(s); return VGW_RX_REJECT; }
vgw_rx_result vgw_recovery_rx_feed(vgw_recovery_rx *s, uint32_t id, bool ext, bool rtr,
                                  const uint8_t *frame, size_t dlc, uint32_t now) {
  if (!s || !s->configured) return VGW_RX_REJECT;
  if (ext || id!=s->request_id) return VGW_RX_IGNORE;
  if (s->complete) return VGW_RX_IGNORE; /* Consumer owns completed message. */
  if (vgw_recovery_rx_expire(s,now)) return VGW_RX_REJECT;
  if (rtr || !frame || dlc!=8) return reject(s);
  unsigned kind=frame[0]>>4;
  if (kind==0) {
    unsigned n=frame[0]&15;
    if (s->active || !n || n>7) return reject(s);
    for (unsigned i=n+1;i<8;i++) if (frame[i]) return reject(s);
    for (unsigned i=0;i<n;i++) s->data[i]=frame[i+1];
    s->offset=s->length=(uint16_t)n; s->complete=true;
    return VGW_RX_COMPLETE;
  }
  if (kind==1) {
    unsigned n=((frame[0]&15U)<<8)|frame[1];
    if (s->active || n<8 || n>VGW_RECOVERY_PDU_MAX) return reject(s);
    s->length=(uint16_t)n; s->offset=6; s->active=true; s->started=s->last=now;
    s->expected=1; s->block=0;
    for (unsigned i=0;i<6;i++) s->data[i]=frame[i+2];
    return VGW_RX_FLOW;
  }
  if (kind!=2 || !s->active || (frame[0]&15)!=s->expected) return reject(s);
  unsigned n=s->length-s->offset;
  if (n>7) n=7;
  for (unsigned i=n+1;i<8;i++) if (frame[i]) return reject(s);
  for (unsigned i=0;i<n;i++) s->data[s->offset+i]=frame[i+1];
  s->offset+=(uint16_t)n; s->last=now; s->expected=(s->expected+1U)&15U;
  if (s->offset==s->length) { s->active=false; s->complete=true; return VGW_RX_COMPLETE; }
  if (++s->block==8) { s->block=0; return VGW_RX_FLOW; }
  return VGW_RX_MORE;
}
