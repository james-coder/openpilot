#ifndef VGW_STATUS_LED_H
#define VGW_STATUS_LED_H
#include <stdbool.h>
#include <stdint.h>

/* Local indication only. Never authorizes TX, confirms an image, feeds a
 * watchdog or makes an optional feature a driving dependency. No ISR owner. */
enum vgw_led_color { VGW_LED_RED=1, VGW_LED_GREEN=2, VGW_LED_BLUE=4 };
enum vgw_led_state { VGW_LED_BOOT=0, VGW_LED_RUNNING, VGW_LED_TRIAL,
  VGW_LED_RECOVERY, VGW_LED_UPDATE, VGW_LED_FAULT, VGW_LED_UPDATE_WAIT,
  VGW_LED_UPDATE_VERIFY, VGW_LED_UPDATE_COMMIT, VGW_LED_UPDATE_READY, VGW_LED_UPDATE_ERASE };
enum vgw_led_error { VGW_LED_NO_IMAGE=1, VGW_LED_IMAGE_POLICY=2,
  VGW_LED_STORAGE=3, VGW_LED_CONFIG=4, VGW_LED_CAN=5,
  VGW_LED_WATCHDOG=6, VGW_LED_CRYPTO=7, VGW_LED_INTERNAL=8, VGW_LED_UPDATE_ABORT=9 };
typedef struct {
  uint32_t since_ms, intro_ms;
  uint8_t state, slot, error, intro_active, intro_seen, intro_slot;
} vgw_status_led;
void vgw_led_init(vgw_status_led *, uint32_t now_ms);
/* Repeated identical status does not restart the pattern. Invalid input
 * displays INTERNAL fault. Slot is 0=A, 1=B, 255=unknown/not applicable. */
void vgw_led_set(vgw_status_led *, unsigned state, unsigned slot, unsigned error, uint32_t now_ms);
/* O(1), no I/O: returns RGB bits, skips missed ticks, handles uint32 wrap.
 * First verified selection plays A=R,G,B / B=R,B,G once, 800ms per color,
 * 200ms gaps and 1s dark before normal status. Fault/update/recovery preempt it.
 * Call at ~20 Hz; never delay CAN work to catch up. Sampling retires the intro
 * so a later timestamp wrap cannot replay it. Preserve this state across the
 * loader/application handoff; only a genuine restart should reinitialize it. */
uint8_t vgw_led_sample(vgw_status_led *, uint32_t now_ms);
/* Historical White Panda only: active-low PC9 red, PC7 green, PC6 blue.
 * Returns ONE atomic GPIOC BSRR value touching ONLY those pins. Caller owns
 * board identification, clock/pin initialization and actual register write. */
uint32_t vgw_white_led_bsrr(uint8_t rgb);
#endif
