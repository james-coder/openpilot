#include "status_led.h"
#include <stddef.h>

void vgw_led_init(vgw_status_led *s, uint32_t now) {
  if (s) *s = (vgw_status_led){.since_ms=now, .state=VGW_LED_BOOT, .slot=255, .intro_slot=255};
}
void vgw_led_set(vgw_status_led *s, unsigned state, unsigned slot, unsigned error, uint32_t now) {
  if (!s) return;
  bool slot_valid = slot < 2 || slot == 255;
  bool valid = state <= VGW_LED_PROBE && slot_valid && (state != VGW_LED_PROBE || slot == 255) &&
    ((state == VGW_LED_FAULT && error >= 1 && error <= 9) || (state != VGW_LED_FAULT && error == 0));
  if (!valid) { state = VGW_LED_FAULT; slot = 255; error = VGW_LED_INTERNAL; }
  if (s->state == state && s->slot == slot && s->error == error) return;
  s->since_ms=now; s->state=(uint8_t)state; s->slot=(uint8_t)slot; s->error=(uint8_t)error;
  bool application = state == VGW_LED_RUNNING || state == VGW_LED_TRIAL || state == VGW_LED_PROBE;
  if (application && (slot < 2 || state == VGW_LED_PROBE) && !s->intro_seen) {
    s->intro_ms=now; s->intro_slot=(uint8_t)slot; s->intro_active=1; s->intro_seen=1;
  }
  if (!application || slot != s->intro_slot) s->intro_active=0;
}
static uint8_t pulses(uint32_t phase, unsigned count, uint8_t color) {
  return phase / 300U < count && phase % 300U < 150U ? color : 0;
}
uint8_t vgw_led_sample(vgw_status_led *s, uint32_t now) {
  if (!s) return 0;
  uint32_t elapsed = now - s->since_ms;
  switch (s->state) {
    case VGW_LED_BOOT: return elapsed % 1000U < 500U ? VGW_LED_BLUE : 0;
    case VGW_LED_RUNNING:
    case VGW_LED_TRIAL:
    case VGW_LED_PROBE:
      if (s->slot != 0 && s->slot != 1 && s->slot != 255) break;
      if (s->intro_active && s->slot == s->intro_slot && (s->slot < 2 || s->state == VGW_LED_PROBE)) {
        uint32_t intro = now - s->intro_ms;
        if (intro < 4000U) {
          if (intro >= 3000U || intro % 1000U >= 800U) return 0;
          static const uint8_t colors[2][3] = {{VGW_LED_RED,VGW_LED_GREEN,VGW_LED_BLUE},
                                              {VGW_LED_RED,VGW_LED_BLUE,VGW_LED_GREEN}};
          return colors[s->slot < 2 ? s->slot : 0][intro / 1000U];
        }
        s->intro_active=0;
      }
      return pulses(elapsed % 4000U, s->state == VGW_LED_PROBE ? 1U : s->slot < 2 ? s->slot + 1U : 3U,
                    s->state == VGW_LED_TRIAL ? VGW_LED_GREEN : VGW_LED_BLUE);
    case VGW_LED_RECOVERY: return elapsed % 4000U < 1000U ? VGW_LED_RED : 0;
    case VGW_LED_UPDATE: return elapsed % 1000U < 500U ? VGW_LED_BLUE : VGW_LED_GREEN;
    case VGW_LED_UPDATE_WAIT: return elapsed % 2000U < 150U ? VGW_LED_BLUE : 0;
    case VGW_LED_UPDATE_VERIFY: return elapsed % 1000U < 800U ? VGW_LED_BLUE : 0;
    case VGW_LED_UPDATE_COMMIT: return VGW_LED_RED | VGW_LED_BLUE;
    case VGW_LED_UPDATE_READY: return pulses(elapsed % 4000U, 4, VGW_LED_GREEN);
    case VGW_LED_UPDATE_ERASE: return elapsed % 1000U < 500U ? VGW_LED_RED | VGW_LED_GREEN : 0;
    case VGW_LED_FAULT:
      if (s->error < 1 || s->error > 9) break;
      return pulses(elapsed % 4000U, s->error, VGW_LED_RED);
    default: break;
  }
  return pulses(elapsed % 4000U, VGW_LED_INTERNAL, VGW_LED_RED);
}
uint32_t vgw_white_led_bsrr(uint8_t rgb) {
  uint32_t on = ((rgb & VGW_LED_RED) ? 1U << 9 : 0) |
                ((rgb & VGW_LED_GREEN) ? 1U << 7 : 0) |
                ((rgb & VGW_LED_BLUE) ? 1U << 6 : 0);
  uint32_t pins = (1U << 9) | (1U << 7) | (1U << 6);
  return (pins & ~on) | (on << 16);
}
