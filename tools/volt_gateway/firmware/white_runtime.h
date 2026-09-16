#ifndef VGW_WHITE_RUNTIME_H
#define VGW_WHITE_RUNTIME_H
#include "white_can.h"
#include "white_rng.h"
#include "recovery_link.h"
/* Cooperative board runtime. No default successful protocol handler. A real
 * authenticated dispatcher must be supplied before this can be a product.
 * This loop is NOT flash-busy safe: do not use it as the flash service callback. */
typedef struct vgw_white_runtime {
  vgw_white_startup startup;
  vgw_white_clock clock;
  vgw_white_rng rng;
  vgw_white_can can;
  vgw_observer observer;
  vgw_recovery_link recovery;
  uint8_t boot_nonce[32];
  bool ready;
  vgw_can_receive listener;
  void *listener_context;
  bool local_control;
} vgw_white_runtime;
typedef bool (*vgw_runtime_protocol)(void *,vgw_white_runtime *);
size_t vgw_white_runtime_size(void);
bool vgw_white_runtime_init(vgw_white_runtime *,const vgw_white_mmio *,const vgw_white_can_config *,uint16_t request_id);
bool vgw_white_runtime_step(vgw_white_runtime *,vgw_runtime_protocol,void *);
void vgw_white_runtime_stop(vgw_white_runtime *);
#endif
