#ifndef VGW_WHITE_WATCHDOG_H
#define VGW_WHITE_WATCHDOG_H
#include "white_board.h"

/* Trusted local task completion, never bits accepted from a host/CAN message.
 * RX completion means the bounded RX service ran, not that traffic arrived.
 * Quiet buses and idle recovery must remain healthy. */
#define VGW_PROGRESS_SCHEDULER 1U
#define VGW_PROGRESS_RX 2U
#define VGW_PROGRESS_PROTOCOL 4U
#define VGW_PROGRESS_HEALTH 8U
#define VGW_PROGRESS_ALL 15U
typedef struct {
  vgw_white_mmio io;
  uint32_t epoch_ms, seen, reset_flags;
  bool started, failed;
} vgw_white_watchdog;

/* Independent watchdog: /32, reload=1999, nominal 2s at 32kHz LSI.
 * Actual oscillator tolerance/timing and debugger behavior need bench tests.
 * Start once early; no API to disable it. No option bytes changed.
 * io callbacks/state and all service callers must be RAM-safe during flash.
 * now_ms must come from a free-running hardware timer, not interrupt ticks. */
bool vgw_white_watchdog_start(vgw_white_watchdog *, const vgw_white_mmio *, uint32_t now_ms);
void vgw_white_watchdog_progress(vgw_white_watchdog *, uint32_t completed);
/* Feed after >=50ms only if all required work completed; latch failure after
 * 250ms without a complete epoch. False requires immediate TX quiescence by
 * the owner; no further feeds occur, and IWDG remains the last resort. */
bool vgw_white_watchdog_service(vgw_white_watchdog *, uint32_t now_ms);
void vgw_white_watchdog_fail(vgw_white_watchdog *);
bool vgw_white_watchdog_was_reset(const vgw_white_watchdog *);
#endif
