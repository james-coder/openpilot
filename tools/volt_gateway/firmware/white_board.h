#ifndef VGW_WHITE_BOARD_H
#define VGW_WHITE_BOARD_H
#include <stdbool.h>
#include <stdint.h>

/* Historical White/F413 candidate only. Trusted MMIO binding, never wire
 * addresses. No flash writes, CAN transmission, clocks/PLL or boot handoff. */
typedef struct {
  void *ctx;
  uint32_t (*read32)(void *, uint32_t);
  uint16_t (*read16)(void *, uint32_t);
  void (*write32)(void *, uint32_t, uint32_t);
} vgw_white_mmio;
typedef struct { uint16_t device_id, revision_id, flash_kib; } vgw_white_identity;
bool vgw_white_identify(const vgw_white_mmio *, vgw_white_identity *);
/* Disable transceivers BEFORE disconnecting AF mux/holding controllers reset.
 * Known board only. Must run before any other code enables a CAN peripheral. */
bool vgw_white_quiesce(const vgw_white_mmio *);
void vgw_white_led(const vgw_white_mmio *, uint8_t rgb);
#endif
