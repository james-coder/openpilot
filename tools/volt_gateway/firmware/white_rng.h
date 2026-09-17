#ifndef VGW_WHITE_RNG_H
#define VGW_WHITE_RNG_H
#include "white_clock.h"
typedef struct {
  const vgw_white_clock *clock;
  uint32_t previous;
  bool ready, failed;
} vgw_white_rng;
/* Polling hardware RNG, no ISR; cold initialization discards first sample.
 * Any status error, timeout, repeated word or clock mismatch latches failure.
 * Continuous comparison is a stuck-output check, not an entropy certification.
 * Failure zeroes output. No deterministic/time/UID fallback is permitted. */
bool vgw_white_rng_init(vgw_white_rng *, const vgw_white_clock *);
/* Non-consuming periodic status/configuration check; failure is latched. */
bool vgw_white_rng_health(vgw_white_rng *);
bool vgw_white_rng_nonce(vgw_white_rng *, uint8_t out[32]);
#endif
