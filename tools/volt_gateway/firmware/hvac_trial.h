#ifndef VGW_HVAC_TRIAL_H
#define VGW_HVAC_TRIAL_H
#include "white_safety.h"
/* Experimental, exact captured center-stack action only. Not SET_RECIRC,
 * not a raw CAN API. Never called by the trusted recovery loader. */
typedef struct {
  vgw_white_safety *safety;
  uint64_t deadline, cooldown, previous;
  uint8_t state, attempts;
  bool allowed;
} vgw_hvac_trial;
/* State: idle, press pending, release wait, release pending, done, fault. */
void vgw_hvac_trial_init(vgw_hvac_trial *,vgw_white_safety *);
void vgw_hvac_trial_step(vgw_hvac_trial *,uint64_t,bool authorized);
bool vgw_hvac_trial_start(vgw_hvac_trial *,uint64_t);
/* Secondary-gateway reset, not a driving-process restart. Does not require
 * good VIN or a cleared RX latch, but must have fresh parked vehicle evidence. */
bool vgw_hvac_trial_can_restart(const vgw_hvac_trial *,uint64_t);
#endif
