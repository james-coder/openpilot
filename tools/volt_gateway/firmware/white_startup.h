#ifndef VGW_WHITE_STARTUP_H
#define VGW_WHITE_STARTUP_H
#include "white_watchdog.h"
typedef struct {
  vgw_white_identity identity;
  vgw_white_watchdog watchdog;
  bool ready;
} vgw_white_startup;
/* Cold-loader candidate, NOT a callable on-road reconfiguration routine.
 * All vehicle transceivers remain disabled. HSI=16MHz is a safe initial clock,
 * not the approved CAN/USB clock. Set the final verified HSE/PLL clock before
 * enabling communications and reconfigure TIM2 consistently at that point.
 * Does not jump to an app, initialize entropy or enable recovery transport. */
bool vgw_white_startup_init(vgw_white_startup *, const vgw_white_mmio *);
/* Protected USB-only recovery entry: CAN stays reset, no watchdog started yet.
 * A bounded recovery window must end in normal startup or ROM recovery; this
 * state must never authorize CAN initialization. */
bool vgw_white_startup_usb_only(vgw_white_startup *,const vgw_white_mmio *);
uint32_t vgw_white_startup_now(const vgw_white_startup *);
#endif
