#ifndef VGW_BOOT_PORT_H
#define VGW_BOOT_PORT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "status_led.h"

/* OFF-DEVICE CANDIDATE. STM32F413/423 RM0430 Table 5 geometry, conditional on
 * identifying the actual chip. First 256 KiB are excluded from these APIs;
 * no final loader/config/provisioning allocation is approved by this layout. */
#include "../geometry.h"
#define VGW_BOOT_FLASH_SIZE VGW_FLASH_BYTES
#define VGW_BOOT_FLASH_BASE 0x08000000U
#define VGW_BOOT_SLOT0 0x40000U
#define VGW_BOOT_SLOT1 VGW_SLOT_B
#define VGW_BOOT_SLOT_SIZE VGW_SLOT_BYTES
#define VGW_BOOT_SECTOR_SIZE 0x20000U
#define VGW_BOOT_HEADER_SIZE 512U

/* Board-owned functions and verification key, never wire/config inputs.
 * service must maintain required clocks/watchdog/RX without reentering boot.
 * panic must disable optional transmissions and must not return. */
typedef struct {
  void *context;
  bool (*read)(void *, uint32_t offset, void *, uint32_t size);
  bool (*write)(void *, uint32_t offset, const void *, uint32_t size);
  bool (*erase)(void *, uint32_t offset, uint32_t size);
  void (*service)(void *);
  void (*panic)(void *);
} vgw_boot_io;

typedef struct { uint32_t slot, vector_address, stack_pointer, reset_handler; } vgw_boot_choice;
/* Signed protected TLV 0xa0: exact 12-byte identity + 32-byte layout digest. */
bool vgw_boot_init(const vgw_boot_io *, const uint8_t public_der[91], const uint8_t target[44]);
/* 0/1 = verified selection; -1 = no bootable image; -2 = latched IO error;
 * -3 = invalid configuration/vector/header. No application is jumped to here. */
int vgw_boot_select(vgw_boot_choice *);
/* Application-side attachment after verified loader handoff. Validates the
 * exact executing slot again without running boot_go/revert a second time.
 * Board must supply its fixed VTOR, never a protocol-selected slot/address. */
bool vgw_boot_resume(unsigned slot,uint32_t vector_address);
/* No caller-supplied slot: only the selection from this boot can be confirmed.
 * Board integration must gate this call on actual application self-tests. */
bool vgw_boot_confirm(void);
/* Trusted updater adapter only, NOT an unauthenticated command API.
 * Before calling: valid PROGRAM authorization, safe fresh power/offroad state,
 * complete outer-image hash/readback checks and trusted inactive-slot selection.
 * Candidate validation performs no writes. Commit validates again, requires an
 * erased trailer, then writes/readbacks magic LAST. Never commits selected slot.
 * Both-invalid recovery requires a completed boot_select returning no image. */
bool vgw_boot_validate_candidate(unsigned slot, uint32_t image_size, uint32_t version);
bool vgw_boot_commit_candidate(unsigned slot, uint32_t image_size, uint32_t version);
bool vgw_boot_faulted(void);
/* Observational local status; board may render it or ignore it entirely. */
void vgw_boot_get_status(uint8_t out_state_slot_error[3]);
void vgw_boot_service(void);
_Noreturn void vgw_boot_panic(void);
#endif
