#ifndef VGW_RECOVERY_LINK_H
#define VGW_RECOVERY_LINK_H
#include "recovery_transport.h"
/* Serialized bounded ISO-TP reply transport. IDs come from trusted provisioning.
 * No device operations or automatic responses to unrelated traffic. FC is a
 * transport resource grant, not authorization. Max one request and reply. */
typedef bool (*vgw_link_send)(void *,const uint8_t frame[8]);
typedef struct {
  vgw_recovery_rx rx;
  uint8_t reply[VGW_RECOVERY_PDU_MAX];
  uint16_t length, offset, separation_ms;
  uint32_t started, last, previous;
  uint32_t rejects, timeouts, completed;
  uint8_t sequence, remaining, waits;
  bool ready, flow_pending, replying, waiting, unlimited;
} vgw_recovery_link;
bool vgw_recovery_link_init(vgw_recovery_link *,uint16_t request_id);
/* Caller owns rx.data on COMPLETE. Keep it owned while arranging a reply.
 * reply() releases the request only after copying the response. For no reply,
 * use rx_clear after processing. Do not act before authenticating whole PDU. */
vgw_rx_result vgw_recovery_link_feed(vgw_recovery_link *,uint32_t id,bool extended,bool rtr,
                                     const uint8_t *,size_t dlc,uint32_t now);
bool vgw_recovery_link_reply(vgw_recovery_link *,const uint8_t *,size_t,uint32_t now);
/* At most one outbound frame/call, advances only if send accepts it. send must
 * be a bounded fixed-ID, rate-limited driver, not an arbitrary TX callback. */
bool vgw_recovery_link_poll(vgw_recovery_link *,uint32_t now,vgw_link_send,void *);
void vgw_recovery_link_close(vgw_recovery_link *);
#endif
