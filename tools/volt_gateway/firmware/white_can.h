#ifndef VGW_WHITE_CAN_H
#define VGW_WHITE_CAN_H
#include "white_clock.h"
#include "observe.h"

/* Fixed trusted board provisioning, never command-controlled. Controller
 * numbers are physical CAN1..3, not openpilot bus indices. Zero backhaul means
 * every controller is listen-only. No vehicle-side TX API exists. */
typedef struct {
  uint8_t swcan_controller, hscan_mask, backhaul_controller;
  uint16_t response_id;
} vgw_white_can_config;
typedef struct {
  uint32_t received, malformed, overflow, transmitted, arbitration_lost, tx_errors;
  uint32_t last_esr, window_start[3], window_bits[3], peak_permille;
  uint32_t software_drops, irq_calls, queue_peak, max_queue_age_ms;
} vgw_white_can_stats;
#define VGW_CAN_RX_DEPTH 64U
typedef struct { uint32_t id, dlc, lo, hi, stamp, sequence; } vgw_white_can_raw;
typedef struct {
  const vgw_white_clock *clock;
  vgw_white_can_config config;
  vgw_white_can_stats stats[3];
  uint32_t previous_ms, token_ms, pending_ms, sequence, config_check;
  uint64_t elapsed_ms;
  uint8_t tokens;
  bool ready, failed, pending, tx_inhibited;
  bool rx_interrupts;
  volatile uint8_t rx_head[3], rx_tail[3], rx_count[3];
  vgw_white_can_raw rx[3][VGW_CAN_RX_DEPTH];
} vgw_white_can;
typedef void (*vgw_can_receive)(void *, const vgw_frame *);
/* Cold init only, all controllers held reset. Dedicated SWCAN PHY PB14/15
 * normal mode, never high-voltage wakeup. CAN3 routing needs board validation. */
bool vgw_white_can_init(vgw_white_can *, const vgw_white_clock *, const vgw_white_can_config *);
/* Main-loop processing: <=8 callbacks/controller per call. IRQs only stage
 * bounded raw records, never invoke protocol/crypto/observer callbacks.
 * Callback must be bounded and must not reenter or retain the frame pointer. */
bool vgw_white_can_poll(vgw_white_can *, vgw_can_receive, void *);
/* Trusted startup only; peripheral interrupt enable, not global/NVIC enable. */
bool vgw_white_can_enable_rx(vgw_white_can *);
/* Fixed RX0 handler, also safe to call with IRQs masked during flash service. */
void vgw_white_can_rx_irq(vgw_white_can *, unsigned controller);
/* Only trusted response scheduler may call this: fixed ID/controller, DLC8.
 * <=100 frames/s + burst2, one mailbox, no auto retry, <=20ms outstanding.
 * No enqueue/backlog; false means defer/drop. Wire authentication is upstream. */
bool vgw_white_can_response(vgw_white_can *, const uint8_t data[8]);
void vgw_white_can_stop(vgw_white_can *);
#endif
