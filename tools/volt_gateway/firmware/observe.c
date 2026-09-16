#include "observe.h"

static uint32_t increment(uint32_t x) { return x == UINT32_MAX ? x : x + 1U; }

static bool identity(uint8_t bus, uint32_t address, uint8_t flags) {
  return bus < 4 && (flags & ~3U) == 0 && address <= ((flags & VGW_EXTENDED) ? 0x1FFFFFFFU : 0x7FFU);
}

static bool clock_ok(vgw_observer *o, uint64_t now) {
  if (now < o->last_us || now >= (UINT64_C(1) << 63)) {
    o->invalid = increment(o->invalid);
    for (unsigned i = 0; i < 4; i++) o->observe_until[i] = 0;
    o->capture_until = 0;
    vgw_clear_subscriptions(o);
    return false;
  }
  o->last_us = now;
  return true;
}

size_t vgw_observer_size(void) { return sizeof(vgw_observer); }

void vgw_observer_init(vgw_observer *o) {
  uint8_t *p = (uint8_t *)o;
  for (size_t i = 0; i < sizeof(*o); i++) p[i] = 0;
}

bool vgw_observe_start(vgw_observer *o, uint8_t bus, uint32_t seconds, uint64_t now) {
  if (bus >= 4 || seconds == 0 || seconds > 3600 || !clock_ok(o, now)) return false;
  o->observe_until[bus] = now + (uint64_t)seconds * 1000000U;
  return true;
}

bool vgw_observe_stop(vgw_observer *o, uint8_t bus) {
  if (bus >= 4) return false;
  o->observe_until[bus] = 0;
  return true;
}

bool vgw_clear_ids(vgw_observer *o, uint8_t bus) {
  if (bus >= 4) return false;
  for (unsigned i = 0; i < VGW_IDS; i++) if (o->ids[i].used && o->ids[i].last.bus == bus) o->ids[i].used = false;
  return true;
}

bool vgw_capture_start(vgw_observer *o, uint8_t mask, uint32_t seconds, uint64_t now) {
  if (!mask || mask > 15 || seconds == 0 || seconds > 3600 || !clock_ok(o, now)) return false;
  o->capture_mask = mask;
  o->capture_count = 0;
  o->capture_until = now + (uint64_t)seconds * 1000000U;
  return true;
}

void vgw_capture_stop(vgw_observer *o) { o->capture_until = 0; }

bool vgw_subscribe(vgw_observer *o, uint8_t handle, uint8_t bus, uint32_t address, uint8_t flags, uint16_t hz) {
  if (handle >= VGW_SUBSCRIPTIONS || !identity(bus, address, flags) || hz == 0 || hz > 1000 ||
      (o->issued_handles & (UINT32_C(1) << handle))) return false;
  vgw_subscription *s = &o->subscriptions[handle];
  *s = (vgw_subscription){0};
  s->bus = bus;
  s->address = address;
  s->flags = flags;
  s->period_us = (1000000U + hz - 1U) / hz;
  s->used = true;
  o->issued_handles |= UINT32_C(1) << handle;
  return true;
}

void vgw_unsubscribe(vgw_observer *o, uint8_t handle) {
  if (handle < VGW_SUBSCRIPTIONS) o->subscriptions[handle] = (vgw_subscription){0};
}

void vgw_clear_subscriptions(vgw_observer *o) {
  for (unsigned i = 0; i < VGW_SUBSCRIPTIONS; i++) vgw_unsubscribe(o, (uint8_t)i);
}

static void statistics(vgw_observer *o, const vgw_frame *f) {
  vgw_id_stat *free_slot = 0;
  for (unsigned i = 0; i < VGW_IDS; i++) {
    vgw_id_stat *s = &o->ids[i];
    if (!s->used) {
      if (!free_slot) free_slot = s;
    } else if (s->last.address == f->address && s->last.bus == f->bus && s->last.flags == f->flags) {
      uint8_t max_dlc = f->dlc > s->last.dlc ? f->dlc : s->last.dlc;
      for (unsigned j = 0; j < max_dlc; j++) {
        if (f->data[j] != s->last.data[j] || (j < f->dlc) != (j < s->last.dlc)) {
          s->changed_mask |= (uint8_t)(1U << j);
          if (s->changes[j] != UINT16_MAX) s->changes[j]++;
        }
      }
      s->last = *f;
      s->count = increment(s->count);
      s->dlc_mask |= (uint16_t)(1U << f->dlc);
      return;
    }
  }
  if (!free_slot) {
    o->id_drops = increment(o->id_drops);
    return;
  }
  *free_slot = (vgw_id_stat){0};
  free_slot->last = *f;
  free_slot->first_us = f->timestamp_us;
  free_slot->count = 1;
  free_slot->dlc_mask = (uint16_t)(1U << f->dlc);
  free_slot->used = true;
}

bool vgw_observer_feed(vgw_observer *o, const vgw_frame *input) {
  if (!identity(input->bus, input->address, input->flags) || input->dlc > 8) {
    o->invalid = increment(o->invalid);
    return false;
  }
  if (!clock_ok(o, input->timestamp_us)) return false;
  vgw_frame f = {0};
  f.timestamp_us = input->timestamp_us;
  f.address = input->address;
  f.bus = input->bus;
  f.flags = input->flags;
  f.dlc = input->dlc;
  f.sequence = o->sequence++;
  if (!(f.flags & VGW_RTR)) for (unsigned i = 0; i < f.dlc; i++) f.data[i] = input->data[i];
  o->received[f.bus] = increment(o->received[f.bus]);
  if (f.timestamp_us < o->observe_until[f.bus]) statistics(o, &f);
  if (f.timestamp_us < o->capture_until && (o->capture_mask & (1U << f.bus))) {
    if (o->capture_count < VGW_CAPTURE) o->capture[o->capture_count++] = f;
    else o->capture_drops = increment(o->capture_drops);
  }
  for (unsigned i = 0; i < VGW_SUBSCRIPTIONS; i++) {
    vgw_subscription *s = &o->subscriptions[i];
    if (s->used && s->bus == f.bus && s->address == f.address && s->flags == f.flags) {
      if (s->pending) s->coalesced = increment(s->coalesced);
      s->latest = f;
      s->pending = true;
    }
  }
  return true;
}

bool vgw_next_observation(vgw_observer *o, uint64_t now, uint32_t age, vgw_frame *out, uint8_t *handle) {
  if (!age || age > 1000000U || !clock_ok(o, now)) return false;
  for (unsigned i = 0; i < VGW_SUBSCRIPTIONS; i++) {
    uint8_t index = (uint8_t)((o->next_subscription + i) % VGW_SUBSCRIPTIONS);
    vgw_subscription *s = &o->subscriptions[index];
    if (!s->used || !s->pending) continue;
    if (now - s->latest.timestamp_us > age) {
      s->pending = false;
      s->stale = increment(s->stale);
    } else if (now >= s->next_us) {
      *out = s->latest;
      *handle = index;
      s->pending = false;
      s->next_us = now + s->period_us;
      o->next_subscription = (uint8_t)((index + 1U) % VGW_SUBSCRIPTIONS);
      return true;
    }
  }
  return false;
}

bool vgw_get_id(vgw_observer *o, uint16_t slot, vgw_id_stat *out) {
  if (slot >= VGW_IDS || !o->ids[slot].used) return false;
  *out = o->ids[slot];
  return true;
}

bool vgw_get_capture(vgw_observer *o, uint16_t index, vgw_frame *out) {
  if (index >= o->capture_count) return false;
  *out = o->capture[index];
  return true;
}
