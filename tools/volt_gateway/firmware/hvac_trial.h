#ifndef VGW_HVAC_TRIAL_H
#define VGW_HVAC_TRIAL_H
#include "white_safety.h"
/* USB-only parked SWCAN experiments. Never called by the recovery loader. */
typedef struct {
  vgw_white_safety *safety;
  uint64_t deadline, cooldown, previous;
  uint8_t state, attempts;
  bool allowed;
  bool raw;
  uint8_t reason;
  uint32_t tx_status, error_status;
} vgw_hvac_trial;
/* State: idle, press pending, release wait, release pending, done, fault,
 * failed raw request (explicit next request allowed after normal checks). */
void vgw_hvac_trial_init(vgw_hvac_trial *,vgw_white_safety *);
void vgw_hvac_trial_step(vgw_hvac_trial *,uint64_t,bool authorized);
bool vgw_hvac_trial_start(vgw_hvac_trial *,uint64_t);
/* One data frame: address BE32, extended flag, DLC, DLC payload bytes.
 * No bus selector, repeat count, remote frames, or automatic retries. */
bool vgw_hvac_trial_raw(vgw_hvac_trial *,uint64_t,const uint8_t *,size_t);
/* Secondary-gateway reset, not a driving-process restart. Does not require
 * good VIN or a cleared RX latch, but must have fresh parked vehicle evidence. */
bool vgw_hvac_trial_can_restart(const vgw_hvac_trial *,uint64_t);
#endif
