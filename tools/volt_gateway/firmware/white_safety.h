#ifndef VGW_WHITE_SAFETY_H
#define VGW_WHITE_SAFETY_H
#include "white_can.h"
/* Read-only inputs. No host command may set these fields. PT bus and ADC
 * divider must come from verified board/harness provisioning, never inference
 * from an arbitrary frame. CAN data is not authenticated: it is an additional
 * operational interlock, NOT a substitute for signed update authorization. */
typedef struct {
  vgw_white_can *can;
  uint64_t received[3], stable_since, previous;
  uint32_t voltage_mv, divider_milli, adc_started, adc_sampled;
  uint8_t powertrain_bus, seen;
  bool safe[3], ready, converting, power_valid, stable;
} vgw_white_safety;
bool vgw_white_safety_init(vgw_white_safety *,vgw_white_can *,uint8_t powertrain_bus,uint32_t divider_milli);
void vgw_white_safety_receive(void *,const vgw_frame *);
bool vgw_white_safety_sample(void *,uint64_t now,uint64_t *sampled,bool *allowed);
#endif
