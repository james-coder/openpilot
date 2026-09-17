#include "authority.h"

static void copy(uint8_t *out, const uint8_t *in, size_t n) {
  for (size_t i = 0; i < n; i++) out[i] = in[i];
}

static bool equal(const uint8_t *a, const uint8_t *b, size_t n) {
  uint8_t different = 0;
  for (size_t i = 0; i < n; i++) different |= a[i] ^ b[i];
  return different == 0;
}

static bool nonzero(const uint8_t *a, size_t n) {
  uint8_t any = 0;
  for (size_t i = 0; i < n; i++) any |= a[i];
  return any != 0;
}

static uint32_t be32(const uint8_t *a) {
  return ((uint32_t)a[0] << 24) | ((uint32_t)a[1] << 16) | ((uint32_t)a[2] << 8) | a[3];
}

size_t vgw_authority_size(void) { return sizeof(vgw_authority); }

void vgw_authority_close(vgw_authority *a) {
  a->closed = true;
  a->pending = a->granted = false;
  a->grant_until = a->challenge_until = 0;
}

static bool clock_ok(vgw_authority *a, uint64_t now) {
  if (a->closed || now >= (UINT64_C(1) << 63) || now < a->last_ms || (a->granted && now >= a->grant_until)) {
    vgw_authority_close(a);
    return false;
  }
  a->last_ms = now;
  return true;
}

bool vgw_authority_init(vgw_authority *a, const uint8_t device[12], const uint8_t layout[32],
                        uint8_t phase, const uint8_t session[32], vgw_verify_fn verify, vgw_sha256_fn sha256, void *context) {
  uint8_t *raw = (uint8_t *)a;
  for (size_t i = 0; i < sizeof(*a); i++) raw[i] = 0;
  a->closed = true;
  if (!verify || !sha256 || (phase != VGW_ENTER && phase != VGW_PROGRAM) || !nonzero(session, 32)) return false;
  copy(a->device, device, 12);
  copy(a->layout, layout, 32);
  copy(a->session, session, 32);
  copy(a->last_nonce, session, 32);
  a->phase = phase;
  a->verify = verify;
  a->sha256 = sha256;
  a->crypto_context = context;
  a->closed = false;
  return true;
}

bool vgw_authority_challenge(vgw_authority *a, const uint8_t *image, size_t len, uint64_t now,
                             const uint8_t nonce[32], uint8_t out[VGW_CHALLENGE_SIZE]) {
  if (!clock_ok(a, now) || a->granted || len != VGW_SIGNED_IMAGE_SIZE) return false;
  if (a->pending && now < a->challenge_until) {
    if (!equal(image, a->image, len)) return false;
    copy(out, a->challenge, VGW_CHALLENGE_SIZE);
    return true;
  }
  if (a->issued && now - a->last_issue_ms < 60000U) return false;
  a->issued = true;
  a->last_issue_ms = now;
  a->pending = false;
  if (!equal(image, (const uint8_t *)"VGIM\x01", 5) || image[17] != 1 ||
      !equal(image + 5, a->device, 12) || !equal(image + 18, a->layout, 32) ||
      be32(image + 50) == 0 || be32(image + 50) > 1048576U ||
      !a->verify(a->crypto_context, true, image, VGW_MANIFEST_SIZE, image + VGW_MANIFEST_SIZE)) return false;
  if (!nonzero(nonce, 32) || equal(nonce, a->last_nonce, 32) || equal(nonce, a->session, 32)) {
    vgw_authority_close(a);
    return false;
  }
  copy(a->last_nonce, nonce, 32);
  copy(a->challenge, (const uint8_t *)"VGCH\x01", 5);
  a->challenge[5] = a->phase;
  copy(a->challenge + 6, a->device, 12);
  copy(a->challenge + 18, a->session, 32);
  copy(a->challenge + 50, nonce, 32);
  copy(a->image, image, len);
  a->sha256(image, VGW_MANIFEST_SIZE, a->digest);
  a->challenge_until = now + 60000U;
  a->pending = true;
  copy(out, a->challenge, VGW_CHALLENGE_SIZE);
  return true;
}

bool vgw_authority_accept(vgw_authority *a, const uint8_t *token, size_t len, uint64_t now) {
  if (!clock_ok(a, now) || a->granted || !a->pending || now >= a->challenge_until || len != VGW_AUTHORIZATION_SIZE) return false;
  if (!equal(token, a->challenge, VGW_CHALLENGE_SIZE) || !equal(token + VGW_CHALLENGE_SIZE, a->digest, 32)) return false;
  uint32_t lease = be32(token + VGW_CHALLENGE_SIZE + 32);
  if (lease < 1000U || lease > 3600000U || (a->verified && now - a->last_verify_ms < 1000U)) return false;
  a->verified = true;
  a->last_verify_ms = now;
  if (!a->verify(a->crypto_context, false, token, len - 64U, token + len - 64U)) return false;
  a->grant_until = now + lease;
  a->granted = true;
  a->pending = false;
  return true;
}

bool vgw_authority_require(vgw_authority *a, uint8_t phase, uint64_t now) {
  return clock_ok(a, now) && a->granted && a->phase == phase;
}
