#ifndef VGW_APPLICATION_H
#define VGW_APPLICATION_H
#include "recovery_service.h"
#include "white_runtime.h"
#include "status_led.h"
/* Default builds have no vehicle TX operations. Decoded telemetry remains untrusted and
 * may never feed actuation without a separate authenticated safety design. */
typedef struct {
  vgw_white_runtime *runtime;
  vgw_recovery_service *session;
  uint64_t lease_until, record_until;
  uint32_t telemetry_drops, telemetry_completed;
  uint16_t sequence;
  uint8_t record[18], fragment;
  bool pending;
  vgw_link_send local_send;
  void *local_context;
  uint8_t indication[5];
  bool indication_valid, can_peer_seen;
  /* Optional board-owned authenticated experiment. NULL in normal/loader builds. */
  size_t (*experiment)(void *,uint8_t,const uint8_t *,size_t,uint64_t,uint8_t *);
  void *experiment_context;
  bool (*local_recovery)(void *);
  void *local_recovery_context;
} vgw_application;
size_t vgw_application_size(void);
bool vgw_application_init(vgw_application *,vgw_white_runtime *,vgw_recovery_service *);
void vgw_application_set_local_recovery(vgw_application *,bool (*)(void *),void *);
size_t vgw_application_command(void *,uint8_t,const uint8_t *,size_t,uint64_t,uint8_t[128]);
void vgw_application_reset(void *);
void vgw_application_indication(vgw_application *,const vgw_status_led *,uint8_t);
bool vgw_application_indicator_snapshot(const vgw_application *,uint8_t[7]);
/* Call after the control transport has had its transmit opportunity. */
bool vgw_application_step(void *,vgw_white_runtime *);
void vgw_application_telemetry(vgw_application *);
#endif
