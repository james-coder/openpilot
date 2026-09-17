#ifndef VOLTGW_OBSERVE_H
#define VOLTGW_OBSERVE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define VGW_IDS 256U
#define VGW_CAPTURE 512U
#define VGW_SUBSCRIPTIONS 32U
#define VGW_EXTENDED 1U
#define VGW_RTR 2U

/* No heap, CAN TX, flash writes or locks. The board adapter must serialize
 * access; never perform a large table copy with CAN interrupts disabled. */
typedef struct {
  uint64_t timestamp_us;
  uint32_t sequence, address;
  uint8_t bus, flags, dlc, data[8];
} vgw_frame;

typedef struct {
  vgw_frame last;
  uint64_t first_us;
  uint32_t count;
  uint16_t changes[8], dlc_mask;
  uint8_t changed_mask;
  bool used;
} vgw_id_stat;

typedef struct {
  vgw_frame latest;
  uint64_t next_us;
  uint32_t address, period_us, coalesced, stale;
  uint8_t bus, flags;
  bool used, pending;
} vgw_subscription;

typedef struct {
  vgw_id_stat ids[VGW_IDS];
  vgw_frame capture[VGW_CAPTURE];
  vgw_subscription subscriptions[VGW_SUBSCRIPTIONS];
  uint64_t observe_until[4], capture_until, last_us;
  uint32_t received[4], id_drops, capture_drops, invalid, sequence, issued_handles;
  uint16_t capture_count;
  uint8_t capture_mask, next_subscription;
  /* Acquisition clocks are ordered per bus, independently of command time. */
  uint64_t frame_last_us[4], observe_since[4], capture_since;
} vgw_observer;

size_t vgw_observer_size(void);
void vgw_observer_init(vgw_observer *);
bool vgw_observe_start(vgw_observer *, uint8_t bus, uint32_t seconds, uint64_t now_us);
bool vgw_observe_stop(vgw_observer *, uint8_t bus);
bool vgw_clear_ids(vgw_observer *, uint8_t bus);
bool vgw_capture_start(vgw_observer *, uint8_t bus_mask, uint32_t seconds, uint64_t now_us);
void vgw_capture_stop(vgw_observer *);
bool vgw_subscribe(vgw_observer *, uint8_t handle, uint8_t bus, uint32_t address, uint8_t flags, uint16_t max_hz);
void vgw_unsubscribe(vgw_observer *, uint8_t handle);
void vgw_clear_subscriptions(vgw_observer *);
bool vgw_observer_feed(vgw_observer *, const vgw_frame *);
bool vgw_next_observation(vgw_observer *, uint64_t now_us, uint32_t max_age_us, vgw_frame *, uint8_t *handle);
bool vgw_get_id(vgw_observer *, uint16_t slot, vgw_id_stat *);
bool vgw_get_capture(vgw_observer *, uint16_t index, vgw_frame *);

#endif
