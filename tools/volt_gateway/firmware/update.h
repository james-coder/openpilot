#ifndef VOLTGW_UPDATE_H
#define VOLTGW_UPDATE_H
#include "authority.h"

/* Trusted, synchronous platform operations. No callback/address comes from CAN.
 * Storage offsets are relative to a fixed INACTIVE slot. Erase sizes must match
 * actual sectors. Writes <=256 bytes; long flash stalls need a board-level plan.
 * Hash callbacks implement streaming SHA256. No default/success crypto stubs. */
typedef struct {
  void *ctx;
  uint32_t capacity;
  uint32_t erase_sizes[16];
  uint8_t erase_count;
  bool (*sample)(void *, uint64_t *now, uint64_t *sampled, bool *allowed);
  bool (*erase)(void *, uint32_t offset, uint32_t size);
  bool (*write)(void *, uint32_t offset, const uint8_t *, size_t);
  bool (*read)(void *, uint32_t offset, uint8_t *, size_t);
  bool (*hash_start)(void *);
  bool (*hash_add)(void *, const uint8_t *, size_t);
  bool (*hash_finish)(void *, uint8_t out[32]);
  bool (*mark_trial)(void *, const uint8_t manifest[VGW_MANIFEST_SIZE]);
} vgw_update_io;

typedef enum { VGW_UPDATE_IDLE, VGW_UPDATE_RECEIVING, VGW_UPDATE_TRIAL, VGW_UPDATE_ABORTED } vgw_update_state;
typedef struct {
  vgw_authority *authority;
  vgw_update_io io;
  uint32_t offset, length, last_offset;
  uint16_t last_size;
  uint8_t last[256], expected[32];
  vgw_update_state state;
} vgw_update;

size_t vgw_update_size(void);
uint32_t vgw_update_offset(const vgw_update *);
vgw_update_state vgw_update_status(const vgw_update *);
bool vgw_update_init(vgw_update *, vgw_authority *, const vgw_update_io *);
void vgw_update_abort(vgw_update *);
bool vgw_update_begin(vgw_update *);
bool vgw_update_chunk(vgw_update *, uint32_t offset, const uint8_t *, size_t);
bool vgw_update_finish(vgw_update *);
#endif
