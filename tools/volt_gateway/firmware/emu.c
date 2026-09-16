/* OFF-DEVICE HARNESS ONLY: no vector table, peripheral setup or release entry.
 * Default crypto traps FAIL unless intercepted. VGW_TARGET_CRYPTO instead
 * executes pinned Mbed TLS on the emulated CPU; still no real peripherals. */
#include "authority.h"
#include "observe.h"
#include "update.h"
#ifdef VGW_TARGET_CRYPTO
#include "crypto.h"
static vgw_crypto crypto;
#define vgw_emu_hash_start vgw_crypto_hash_start
#define vgw_emu_hash_add vgw_crypto_hash_add
#define vgw_emu_hash_finish vgw_crypto_hash_finish
#define vgw_emu_verify vgw_crypto_verify
#define vgw_emu_sha256 vgw_crypto_authority_hash
#define CRYPTO_CONTEXT (&crypto)
#else
#define CRYPTO_CONTEXT 0
#endif

static vgw_authority authority;
static vgw_observer observer;
volatile uint32_t vgw_emu_result;
volatile uint32_t vgw_emu_authorized;
volatile uint32_t vgw_emu_updated;
static vgw_update updater;
static uint8_t slot[64];
static bool trial;

/* Host-backed cryptographic traps, NEVER production implementations. */
#ifndef VGW_TARGET_CRYPTO
__attribute__((noinline)) bool vgw_emu_hash_start(void *ctx) { (void)ctx; return false; }
__attribute__((noinline)) bool vgw_emu_hash_add(void *ctx, const uint8_t *data, size_t size) {
  (void)ctx; (void)data; (void)size; return false;
}
__attribute__((noinline)) bool vgw_emu_hash_finish(void *ctx, uint8_t out[32]) {
  (void)ctx; (void)out; return false;
}
#endif
static bool sample(void *ctx, uint64_t *now, uint64_t *sampled, bool *allowed) {
  (void)ctx; *now = 3; *sampled = 3; *allowed = true; return true;
}
static bool erase(void *ctx, uint32_t offset, uint32_t size) {
  (void)ctx;
  if (offset > sizeof(slot) || size > sizeof(slot) - offset) return false;
  for (uint32_t i = 0; i < size; i++) slot[offset + i] = 255;
  return true;
}
static bool write_slot(void *ctx, uint32_t offset, const uint8_t *data, size_t size) {
  (void)ctx;
  if (offset > sizeof(slot) || size > sizeof(slot) - offset) return false;
  for (size_t i = 0; i < size; i++) {
    if ((slot[offset + i] & data[i]) != data[i]) return false;
    slot[offset + i] = data[i];
  }
  return true;
}
static bool read_slot(void *ctx, uint32_t offset, uint8_t *data, size_t size) {
  (void)ctx;
  if (offset > sizeof(slot) || size > sizeof(slot) - offset) return false;
  for (size_t i = 0; i < size; i++) data[i] = slot[offset + i];
  return true;
}
static bool mark_trial(void *ctx, const uint8_t *manifest) {
  (void)ctx; (void)manifest; trial = true; return true;
}

#ifndef VGW_TARGET_CRYPTO
__attribute__((noinline)) bool vgw_emu_verify(void *ctx, bool image, const uint8_t *p, size_t n, const uint8_t *sig) {
  (void)ctx; (void)image; (void)p; (void)n; (void)sig;
  return false;
}

__attribute__((noinline)) void vgw_emu_sha256(const uint8_t *p, size_t n, uint8_t *out) {
  (void)p; (void)n;
  for (unsigned i = 0; i < 32; i++) out[i] = 0;
}
#endif

/* GCC may emit these for structure copies even with -fno-builtin. */
void *memcpy(void *dest, const void *src, size_t n) {
  uint8_t *d = dest;
  const uint8_t *s = src;
  for (size_t i = 0; i < n; i++) d[i] = s[i];
  return dest;
}
void *memset(void *dest, int value, size_t n) {
  uint8_t *d = dest;
  for (size_t i = 0; i < n; i++) d[i] = (uint8_t)value;
  return dest;
}

int memcmp(const void *left, const void *right, size_t n) {
  const uint8_t *a = left, *b = right;
  for (size_t i = 0; i < n; i++) if (a[i] != b[i]) return (int)a[i] - (int)b[i];
  return 0;
}

void *memmove(void *dest, const void *src, size_t n) {
  uint8_t *d = dest;
  const uint8_t *s = src;
  if ((uintptr_t)d <= (uintptr_t)s) {
    for (size_t i = 0; i < n; i++) d[i] = s[i];
  } else {
    for (size_t i = n; i > 0; i--) d[i-1] = s[i-1];
  }
  return dest;
}

__attribute__((noinline,noreturn)) void vgw_emu_done(void) { for (;;) __asm__ volatile ("nop"); }

void vgw_emu_entry(void) {
  const uint8_t *vectors = (const uint8_t *)0x20018000U;
#ifdef VGW_TARGET_CRYPTO
  if (!vgw_crypto_init(&crypto, vectors + VGW_SIGNED_IMAGE_SIZE + VGW_AUTHORIZATION_SIZE, 65)) {
    vgw_emu_result = 32768;
    vgw_emu_done();
  }
  /* Public known-answer fixture, NOT any deployed operational secret. */
  const uint8_t expected_mac[32] = {
    0xaf,0x61,0xb6,0x93,0x91,0x2e,0xfc,0x56,0xe2,0xe4,0x6f,0x94,0x97,0x19,0xea,0x10,
    0xa9,0xe8,0x0d,0x68,0xf8,0xdc,0xfb,0x84,0xe2,0x6c,0xac,0x53,0x30,0xda,0x3c,0x74
  };
  uint8_t test_key[32], mac[32];
  for (unsigned i = 0; i < sizeof(test_key); i++) test_key[i] = 'k';
  if (!vgw_crypto_hmac(test_key, (const uint8_t *)"abc", 3, mac) || memcmp(mac, expected_mac, 32)) {
    vgw_emu_result = 65536;
    vgw_emu_done();
  }
#endif
  uint8_t device[12], layout[32], session[32], nonce[32], challenge[VGW_CHALLENGE_SIZE];
  for (unsigned i = 0; i < 12; i++) device[i] = 'd';
  for (unsigned i = 0; i < 32; i++) { layout[i] = 'l'; session[i] = 's'; nonce[i] = 'n'; }
  uint32_t failures = 0;
  if (!vgw_authority_init(&authority, device, layout, VGW_PROGRAM, session, vgw_emu_verify, vgw_emu_sha256, CRYPTO_CONTEXT)) failures |= 1;
  if (!vgw_authority_challenge(&authority, vectors, VGW_SIGNED_IMAGE_SIZE, 0, nonce, challenge)) failures |= 2;
  bool accepted = vgw_authority_accept(&authority, vectors + VGW_SIGNED_IMAGE_SIZE, VGW_AUTHORIZATION_SIZE, 0);
  vgw_emu_authorized = accepted ? 1U : 0U;
  if (vgw_authority_require(&authority, VGW_PROGRAM, 1) != accepted) failures |= 4;
  if (vgw_authority_accept(&authority, vectors + VGW_SIGNED_IMAGE_SIZE, VGW_AUTHORIZATION_SIZE, 1)) failures |= 8;
  if (vgw_authority_require(&authority, VGW_ENTER, 2)) failures |= 16;
  vgw_update_io io = {.ctx=CRYPTO_CONTEXT, .capacity=sizeof(slot), .erase_sizes={32,32}, .erase_count=2,
    .sample=sample, .erase=erase, .write=write_slot, .read=read_slot,
    .hash_start=vgw_emu_hash_start, .hash_add=vgw_emu_hash_add, .hash_finish=vgw_emu_hash_finish, .mark_trial=mark_trial};
  if (!vgw_update_init(&updater, &authority, &io)) failures |= 4096;
  if (accepted) {
    const uint8_t body[] = "public emulator image fixture";
    if (!vgw_update_begin(&updater) || !vgw_update_chunk(&updater, 0, body, sizeof(body)-1) ||
        !vgw_update_chunk(&updater, 0, body, sizeof(body)-1) || !vgw_update_finish(&updater) || !trial) failures |= 8192;
    vgw_emu_updated = trial ? 1U : 0U;
  } else if (vgw_update_begin(&updater) || trial) {
    failures |= 16384;
  }
  if (vgw_authority_require(&authority, VGW_PROGRAM, 3600001U)) failures |= 32;

  vgw_observer_init(&observer);
  vgw_frame f = {.timestamp_us=1, .address=0x123, .bus=3, .dlc=8};
  vgw_id_stat stat;
  vgw_frame output;
  uint8_t handle;
  if (!vgw_observe_start(&observer, 3, 30, 0) || !vgw_capture_start(&observer, 8, 30, 0) ||
      !vgw_subscribe(&observer, 0, 3, 0x123, 0, 10)) failures |= 64;
  if (!vgw_observer_feed(&observer, &f)) failures |= 128;
  f.timestamp_us = 2; f.data[7] = 1;
  if (!vgw_observer_feed(&observer, &f)) failures |= 128;
  if (!vgw_get_id(&observer, 0, &stat) || stat.count != 2 || stat.changed_mask != 128) failures |= 256;
  if (!vgw_next_observation(&observer, 2, 1000, &output, &handle) || handle != 0 || output.data[7] != 1) failures |= 512;
  if (vgw_next_observation(&observer, 2, 1000, &output, &handle)) failures |= 1024;
  vgw_clear_subscriptions(&observer);
  if (vgw_subscribe(&observer, 0, 3, 0x456, 0, 10)) failures |= 2048;
  vgw_emu_result = failures;
  vgw_emu_done();
}
