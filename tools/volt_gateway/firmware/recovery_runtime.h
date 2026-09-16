#ifndef VGW_RECOVERY_RUNTIME_H
#define VGW_RECOVERY_RUNTIME_H
#include "white_runtime.h"
#include "recovery_service.h"
/* Actual runtime protocol callback: context points to an initialized trusted
 * recovery_service. No permissive/no-command production fallback. */
bool vgw_recovery_runtime_step(void *,vgw_white_runtime *);
#endif
