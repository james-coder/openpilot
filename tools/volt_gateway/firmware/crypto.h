#ifndef VOLTGW_CRYPTO_H
#define VOLTGW_CRYPTO_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "mbedtls/sha256.h"

/* Single cooperative owner only. Never invoke from an ISR. No private ECDSA
 * key is present. Public key is an exact SEC1 uncompressed P-256 point.
 * The fixed arena belongs to this backend; exhaustion never falls back to
 * libc malloc. Board integration must bound verification latency and arrange
 * watchdog/TX shutdown on panic. This is NOT an entropy/RNG implementation. */
#define VGW_CRYPTO_ARENA_SIZE 24576U
typedef struct {
  uint8_t public_key[65];
  mbedtls_sha256_context stream;
  bool ready, streaming;
} vgw_crypto;

size_t vgw_crypto_size(void);
bool vgw_crypto_init(vgw_crypto *, const uint8_t *, size_t);
void vgw_crypto_free(vgw_crypto *);
bool vgw_crypto_verify(void *, bool image, const uint8_t *, size_t, const uint8_t signature[64]);
bool vgw_crypto_sha256(const uint8_t *, size_t, uint8_t out[32]);
/* Legacy authority callback: an internal hash error latches verification off. */
void vgw_crypto_authority_hash(const uint8_t *, size_t, uint8_t out[32]);
bool vgw_crypto_hash_start(void *);
bool vgw_crypto_hash_add(void *, const uint8_t *, size_t);
bool vgw_crypto_hash_finish(void *, uint8_t out[32]);
bool vgw_crypto_hmac(const uint8_t key[32], const uint8_t *, size_t, uint8_t out[32]);
/* Board-only cooperative verifier binding. The callback must not enter any
 * cryptography or dispatch requests; service bounded RX/watchdog work only. */
bool vgw_crypto_cooperative_init(bool (*service)(void *),void *);
#endif
