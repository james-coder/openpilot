#ifndef VGW_APPLICATION_H
#define VGW_APPLICATION_H
#include "recovery_service.h"
#include "white_runtime.h"
/* No vehicle TX operations. Read-only decoded telemetry remains untrusted and
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
} vgw_application;
size_t vgw_application_size(void);
bool vgw_application_init(vgw_application *,vgw_white_runtime *,vgw_recovery_service *);
size_t vgw_application_command(void *,uint8_t,const uint8_t *,size_t,uint64_t,uint8_t[128]);
void vgw_application_reset(void *);
/* Call after the control transport has had its transmit opportunity. */
bool vgw_application_step(void *,vgw_white_runtime *);
void vgw_application_telemetry(vgw_application *);
#endif
