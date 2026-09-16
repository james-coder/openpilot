#ifndef VGW_WHITE_FLASH_H
#define VGW_WHITE_FLASH_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* F413 candidate. Trusted board bindings, NEVER supplied by CAN. All callbacks
 * and their transitive dependencies used during an operation MUST run in SRAM.
 * enter/leave preserve interrupt ownership; enter must mask flash-resident IRQs.
 * permit must fail on stale/unsafe power, expired update permission or sleep.
 * service maintains bounded RAM-resident RX/watchdog work, never starts TX.
 * now_ms is a free-running hardware clock (not an IRQ-dependent software tick).
 * fatal disables optional TX and resets/halts in RAM; it MUST NOT return.
 * These obligations are not implemented by this callback interface. */
typedef struct {
  void *ctx;
  uint32_t (*reg_read)(void *, uint32_t);
  bool (*reg_write)(void *, uint32_t, uint32_t);
  bool (*read)(void *, uint32_t, uint8_t *, uint32_t);
  bool (*program8)(void *, uint32_t, uint8_t);
  uint32_t (*now_ms)(void *);
  bool (*permit)(void *);
  bool (*enter)(void *);
  void (*leave)(void *);
  void (*service)(void *);
  void (*fatal)(void *);
  uint32_t poll_limit; /* trusted calibrated bound, 1..50,000,000 */
} vgw_flash_io;
typedef struct {
  vgw_flash_io io;
  uint32_t base, acr, last_sr;
  bool ready, failed, active, entered, cache_changed, image_mutation;
} vgw_white_flash;

size_t vgw_white_flash_size(void);
/* slot=0/1; image_mutation=false for a protected active/fallback image.
 * Trusted loader determines role. This flag is NOT a wire command/authorization. */
bool vgw_white_flash_init(vgw_white_flash *, const vgw_flash_io *, unsigned slot, bool image_mutation);
bool vgw_white_flash_read(vgw_white_flash *, uint32_t off, uint8_t *, uint32_t size);
bool vgw_white_flash_erase(vgw_white_flash *, uint32_t off); /* exactly one 128KiB sector */
bool vgw_white_flash_program(vgw_white_flash *, uint32_t off, const uint8_t *, uint32_t size);
/* Separate metadata path: only copy_done, image_ok or exact boot magic. Caller
 * must first complete inner signature/target validation and update authorization. */
bool vgw_white_flash_trailer(vgw_white_flash *, uint32_t off, const uint8_t *, uint32_t size);
bool vgw_white_flash_failed(const vgw_white_flash *);
#endif
