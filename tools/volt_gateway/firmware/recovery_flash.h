#ifndef VGW_RECOVERY_FLASH_H
#define VGW_RECOVERY_FLASH_H
#include "recovery_service.h"
#include "white_runtime.h"
/* RAM-only callbacks for white_platform's flash permit/service bindings.
 * Safety source and receive decoder must be vetted board functions, not CAN
 * commands or host-supplied booleans. sample must observe actual fresh inputs.
 * Their entire call graph/data must reside in SRAM while flash is busy. */
typedef struct {
  vgw_white_runtime *runtime;
  vgw_recovery_service *recovery;
  bool (*sample)(void *,uint64_t now,uint64_t *sampled,bool *allowed);
  void (*receive)(void *,const vgw_frame *);
  void *context;
  uint32_t received,dropped_commands;
  bool enabled,failed;
} vgw_recovery_flash;
bool vgw_recovery_flash_init(vgw_recovery_flash *,vgw_white_runtime *,vgw_recovery_service *,
  bool (*sample)(void *,uint64_t,uint64_t *,bool *),void (*receive)(void *,const vgw_frame *),void *);
bool vgw_recovery_flash_permit(void *);
void vgw_recovery_flash_service(void *);
#endif
