#ifndef VOLTGW_AUTHORITY_H
#define VOLTGW_AUTHORITY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define VGW_MANIFEST_SIZE 122U
#define VGW_SIGNED_IMAGE_SIZE 186U
#define VGW_CHALLENGE_SIZE 82U
#define VGW_AUTHORIZATION_SIZE 182U
#define VGW_ENTER 1U
#define VGW_PROGRAM 2U

/* Trusted platform callbacks, NEVER supplied by a protocol command. verify must
 * verify ECDSA-P256/SHA256 of domain || body, with a public-only provisioned key.
 * A real hardware port must supply vetted crypto and a healthy RNG. No defaults
 * or successful stub implementations are provided. */
typedef bool (*vgw_verify_fn)(void *, bool image, const uint8_t *, size_t, const uint8_t *);
typedef void (*vgw_sha256_fn)(const uint8_t *, size_t, uint8_t *);

typedef struct {
  uint8_t device[12], layout[32], session[32], last_nonce[32];
  uint8_t challenge[VGW_CHALLENGE_SIZE], image[VGW_SIGNED_IMAGE_SIZE], digest[32];
  uint64_t last_ms, challenge_until, grant_until, last_issue_ms, last_verify_ms;
  bool issued, verified, pending, granted, closed;
  uint8_t phase;
  vgw_verify_fn verify;
  vgw_sha256_fn sha256;
  void *crypto_context;
} vgw_authority;

size_t vgw_authority_size(void);
bool vgw_authority_init(vgw_authority *, const uint8_t device[12], const uint8_t layout[32],
                        uint8_t phase, const uint8_t session[32], vgw_verify_fn, vgw_sha256_fn, void *);
void vgw_authority_close(vgw_authority *);
bool vgw_authority_challenge(vgw_authority *, const uint8_t *, size_t, uint64_t now_ms,
                             const uint8_t nonce[32], uint8_t out[VGW_CHALLENGE_SIZE]);
bool vgw_authority_accept(vgw_authority *, const uint8_t *, size_t, uint64_t now_ms);
bool vgw_authority_require(vgw_authority *, uint8_t phase, uint64_t now_ms);

#endif
