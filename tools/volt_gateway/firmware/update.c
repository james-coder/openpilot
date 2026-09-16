#include "update.h"
#include "status_led.h"

static bool equal(const uint8_t *a, const uint8_t *b, size_t n) {
  uint8_t d = 0;
  for (size_t i = 0; i < n; i++) d |= a[i] ^ b[i];
  return d == 0;
}

size_t vgw_update_size(void) { return sizeof(vgw_update); }
uint32_t vgw_update_offset(const vgw_update *u) { return u->offset; }
vgw_update_state vgw_update_status(const vgw_update *u) { return u->state; }

void vgw_update_abort(vgw_update *u) {
  u->state = VGW_UPDATE_ABORTED;
  u->phase = VGW_PROGRESS_ABORT;
  u->last_size = 0;
  if (u->authority) vgw_authority_close(u->authority);
}

static bool check(vgw_update *u) {
  uint64_t now = 0, sampled = 0;
  bool allowed = false;
  if (u->state == VGW_UPDATE_ABORTED || u->state == VGW_UPDATE_TRIAL ||
      !u->io.sample(u->io.ctx, &now, &sampled, &allowed) || !allowed ||
      now >= (UINT64_C(1) << 63) || sampled > now || now - sampled > 30U ||
      !vgw_authority_require(u->authority, VGW_PROGRAM, now)) {
    vgw_update_abort(u);
    return false;
  }
  u->checked_ms = now;
  return true;
}

bool vgw_update_init(vgw_update *u, vgw_authority *a, const vgw_update_io *io) {
  *u = (vgw_update){0};
  u->authority = a;
  if (!a || !io || a->phase != VGW_PROGRAM || !io->capacity || io->capacity > 1048576U ||
      !io->erase_count || io->erase_count > 16U || !io->sample || !io->erase || !io->write || !io->read ||
      !io->hash_start || !io->hash_add || !io->hash_finish || !io->mark_trial) {
    vgw_update_abort(u);
    return false;
  }
  uint32_t total = 0;
  for (unsigned i = 0; i < io->erase_count; i++) {
    if (!io->erase_sizes[i] || io->erase_sizes[i] > io->capacity - total) {
      vgw_update_abort(u);
      return false;
    }
    total += io->erase_sizes[i];
  }
  if (total != io->capacity) { vgw_update_abort(u); return false; }
  u->io = *io;
  return true;
}

bool vgw_update_begin(vgw_update *u) {
  if (u->state != VGW_UPDATE_IDLE || !check(u)) return false;
  const uint8_t *m = u->authority->image;
  u->length = ((uint32_t)m[50] << 24) | ((uint32_t)m[51] << 16) | ((uint32_t)m[52] << 8) | m[53];
  if (!u->length || u->length > u->io.capacity) { vgw_update_abort(u); return false; }
  for (unsigned i = 0; i < 32; i++) u->expected[i] = m[54 + i];
  uint32_t offset = 0;
  u->phase = VGW_PROGRESS_ERASE;
  for (unsigned i = 0; i < u->io.erase_count; i++) {
    if (!check(u) || !u->io.erase(u->io.ctx, offset, u->io.erase_sizes[i]) || !check(u)) {
      vgw_update_abort(u); return false;
    }
    offset += u->io.erase_sizes[i];
  }
  if (!u->io.hash_start(u->io.ctx) || !check(u)) { vgw_update_abort(u); return false; }
  u->state = VGW_UPDATE_RECEIVING;
  u->phase = VGW_PROGRESS_RECEIVE;
  u->progress_ms = u->checked_ms;
  return true;
}

bool vgw_update_chunk(vgw_update *u, uint32_t offset, const uint8_t *data, size_t size) {
  if (!check(u) || u->state != VGW_UPDATE_RECEIVING || !data || !size || size > 256U) return false;
  if (u->last_size == size && offset == u->last_offset && equal(data, u->last, size)) return true;
  if (offset != u->offset || offset > u->length || size > u->length - offset) return false;
  if (!u->io.write(u->io.ctx, offset, data, size) || !check(u) ||
      !u->io.hash_add(u->io.ctx, data, size) || !check(u)) {
    vgw_update_abort(u); return false;
  }
  for (size_t i = 0; i < size; i++) u->last[i] = data[i];
  u->last_offset = offset;
  u->last_size = (uint16_t)size;
  u->offset += (uint32_t)size;
  u->progress_ms = u->checked_ms;
  return true;
}

bool vgw_update_finish(vgw_update *u) {
  uint8_t digest[32], data[256];
  if (!check(u)) return false;
  u->phase = VGW_PROGRESS_VERIFY;
  if (u->state != VGW_UPDATE_RECEIVING || u->offset != u->length ||
      !u->io.hash_finish(u->io.ctx, digest) || !check(u) || !equal(digest, u->expected, 32) ||
      !u->io.hash_start(u->io.ctx) || !check(u)) {
    vgw_update_abort(u); return false;
  }
  for (uint32_t offset = 0; offset < u->length; offset += 256U) {
    size_t size = u->length - offset < 256U ? u->length - offset : 256U;
    if (!check(u) || !u->io.read(u->io.ctx, offset, data, size) || !check(u) ||
        !u->io.hash_add(u->io.ctx, data, size) || !check(u)) {
      vgw_update_abort(u); return false;
    }
  }
  if (!u->io.hash_finish(u->io.ctx, digest) || !check(u) || !equal(digest, u->expected, 32)) {
    vgw_update_abort(u); return false;
  }
  u->phase = VGW_PROGRESS_COMMIT;
  if (!u->io.mark_trial(u->io.ctx, u->authority->image) || !check(u)) {
    /* A failed post-commit check cannot undo an already persisted trial. */
    vgw_update_abort(u); return false;
  }
  u->state = VGW_UPDATE_TRIAL;
  u->phase = VGW_PROGRESS_READY;
  vgw_authority_close(u->authority);
  return true;
}

bool vgw_update_led(const vgw_update *u, uint64_t now, uint8_t out[3]) {
  if (!u || !out || u->phase == VGW_PROGRESS_IDLE) return false;
  out[1]=255; out[2]=0;
  switch (u->phase) {
    case VGW_PROGRESS_ERASE: out[0]=VGW_LED_UPDATE_ERASE; break;
    case VGW_PROGRESS_RECEIVE:
      out[0]=(u->offset && now>=u->progress_ms && now-u->progress_ms<=1500U) ? VGW_LED_UPDATE : VGW_LED_UPDATE_WAIT;
      break;
    case VGW_PROGRESS_VERIFY: out[0]=VGW_LED_UPDATE_VERIFY; break;
    case VGW_PROGRESS_COMMIT: out[0]=VGW_LED_UPDATE_COMMIT; break;
    case VGW_PROGRESS_READY: out[0]=VGW_LED_UPDATE_READY; break;
    case VGW_PROGRESS_ABORT: out[0]=VGW_LED_FAULT; out[2]=VGW_LED_UPDATE_ABORT; break;
    default: out[0]=VGW_LED_FAULT; out[2]=VGW_LED_INTERNAL; break;
  }
  return true;
}
