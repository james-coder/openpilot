#ifndef VGW_WHITE_PLATFORM_H
#define VGW_WHITE_PLATFORM_H
#include "white_flash.h"
#include "white_startup.h"
#include "boot/boot_port.h"
/* ARM-only physical bindings. Call only from a reviewed complete board image.
 * service and permit plus all reachable code/data MUST reside in SRAM.
 * service performs bounded scheduler/RX/protocol/health work and reports actual
 * task completion; it must not blindly mark all watchdog progress bits.
 * No addresses or function pointers may originate in a protocol message. */
typedef struct {
  vgw_white_startup *startup;
  bool (*permit)(void *);
  void (*service)(void *);
  void *context;
  uint32_t primask;
  bool entered;
} vgw_white_platform;
const vgw_white_mmio *vgw_white_physical_mmio(void);
/* Physical flash IO bound to this single trusted owner, with fixed poll cap.
 * Caller initializes the slot driver with the correct inactive/metadata role. */
bool vgw_white_physical_flash(vgw_white_platform *, vgw_flash_io *);
/* Last-resort TX disable followed by system reset; RAM-only and never returns. */
_Noreturn void vgw_white_physical_reset(void);
/* Only a verified boot_select result from this boot; NEVER protocol fields.
 * Success never returns. App inherits an active IWDG and masked interrupts,
 * must establish its own runtime/VTOR policy before enabling them. */
bool vgw_white_physical_handoff(const vgw_boot_choice *);
#endif
