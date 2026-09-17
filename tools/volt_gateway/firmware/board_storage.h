#ifndef VGW_BOARD_STORAGE_H
#define VGW_BOARD_STORAGE_H
#include "crypto.h"
#include "recovery_flash.h"
#include "white_safety.h"
#include "white_platform.h"
typedef struct {
  vgw_white_runtime *runtime;
  vgw_recovery_service *recovery;
  vgw_white_safety *safety;
  vgw_white_platform platform;
  vgw_recovery_flash guard;
  vgw_white_flash flash[2];
  vgw_crypto crypto;
  unsigned inactive;
  bool boot_phase, ready;
} vgw_board_storage;
/* Fixed board adapter: no caller-controlled memory address. init assumes
 * trusted verified board configuration and live physical safety sampler. */
bool vgw_board_storage_init(vgw_board_storage *,vgw_white_runtime *,vgw_white_safety *,
                           vgw_recovery_service *,const uint8_t public_der[91],const uint8_t target[44]);
bool vgw_board_storage_updater(vgw_board_storage *,unsigned inactive,vgw_update_io *);
bool vgw_board_storage_recovery_ready(vgw_board_storage *);
#endif
