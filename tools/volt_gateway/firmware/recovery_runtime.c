#include "recovery_runtime.h"
bool vgw_recovery_runtime_step(void *ctx,vgw_white_runtime *runtime) {
  vgw_recovery_service *s=ctx;
  if (!runtime || !runtime->ready || !vgw_recovery_service_tick(s,runtime->can.elapsed_ms)) return false;
  vgw_recovery_link *link=&runtime->recovery;
  if (!link->rx.complete) return true;
  uint8_t reply[512]; size_t size=0;
  bool accepted=vgw_recovery_service_request(s,link->rx.data,link->rx.length,runtime->can.elapsed_ms,reply,&size);
  if (accepted) {
    if (!vgw_recovery_link_reply(link,reply,size,(uint32_t)runtime->can.elapsed_ms)) return false;
  } else vgw_recovery_rx_clear(&link->rx);
  return s->ready;
}
