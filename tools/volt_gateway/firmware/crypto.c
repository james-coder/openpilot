#include "crypto.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/md.h"
#include "mbedtls/memory_buffer_alloc.h"
#include "mbedtls/platform_util.h"
#include <string.h>

static _Alignas(8) unsigned char arena[VGW_CRYPTO_ARENA_SIZE];
static bool initialized, faulted;
static const uint8_t image_domain[] = "VOLT GW FIRMWARE MANIFEST v1";
static const uint8_t auth_domain[] = "VOLT GW UPDATE AUTHORIZATION v1";

/* Heap corruption is fatal. A real board must disable optional TX and reset
 * through its watchdog; never recover by accepting a failed verification. */
_Noreturn void vgw_crypto_panic(int status) {
  (void)status;
  faulted = true;
  for (;;) { __asm__ volatile ("" ::: "memory"); }
}

static void setup(void) {
  if (!initialized) {
    mbedtls_memory_buffer_alloc_init(arena, sizeof(arena));
    mbedtls_memory_buffer_set_verify(MBEDTLS_MEMORY_VERIFY_ALWAYS);
    initialized = true;
  }
}

static bool load_key(mbedtls_ecp_group *group, mbedtls_ecp_point *point, const uint8_t *key) {
  return key[0] == 4 && mbedtls_ecp_group_load(group, MBEDTLS_ECP_DP_SECP256R1) == 0 &&
    mbedtls_ecp_point_read_binary(group, point, key, 65) == 0 && mbedtls_ecp_check_pubkey(group, point) == 0;
}

size_t vgw_crypto_size(void) { return sizeof(vgw_crypto); }

void vgw_crypto_free(vgw_crypto *ctx) {
  if (ctx) mbedtls_platform_zeroize(ctx, sizeof(*ctx));
}

bool vgw_crypto_init(vgw_crypto *ctx, const uint8_t *key, size_t length) {
  if (!ctx) return false;
  vgw_crypto_free(ctx);
  if (!key || length != 65 || faulted) return false;
  setup();
  mbedtls_ecp_group group;
  mbedtls_ecp_point point;
  mbedtls_ecp_group_init(&group);
  mbedtls_ecp_point_init(&point);
  bool ok = load_key(&group, &point, key);
  mbedtls_ecp_point_free(&point);
  mbedtls_ecp_group_free(&group);
  if (ok) {
    memcpy(ctx->public_key, key, 65);
    mbedtls_sha256_init(&ctx->stream);
    ctx->ready = true;
  }
  return ok;
}

bool vgw_crypto_sha256(const uint8_t *data, size_t length, uint8_t out[32]) {
  if (!out) return false;
  memset(out, 0, 32);
  if (faulted || (!data && length)) return false;
  return mbedtls_sha256(data, length, out, 0) == 0;
}

void vgw_crypto_authority_hash(const uint8_t *data, size_t length, uint8_t out[32]) {
  if (!vgw_crypto_sha256(data, length, out)) faulted = true;
}

bool vgw_crypto_verify(void *context, bool image, const uint8_t *data, size_t length, const uint8_t signature[64]) {
  vgw_crypto *ctx = context;
  if (!ctx || !ctx->ready || faulted || !data || !signature || length != (image ? 122U : 118U)) return false;
  uint8_t digest[32];
  mbedtls_sha256_context hash;
  mbedtls_sha256_init(&hash);
  bool ok = mbedtls_sha256_starts(&hash, 0) == 0 &&
    mbedtls_sha256_update(&hash, image ? image_domain : auth_domain, image ? sizeof(image_domain) : sizeof(auth_domain)) == 0 &&
    mbedtls_sha256_update(&hash, data, length) == 0 && mbedtls_sha256_finish(&hash, digest) == 0;
  mbedtls_sha256_free(&hash);
  mbedtls_ecp_group group;
  mbedtls_ecp_point point;
  mbedtls_mpi r, s;
  mbedtls_ecp_group_init(&group);
  mbedtls_ecp_point_init(&point);
  mbedtls_mpi_init(&r);
  mbedtls_mpi_init(&s);
  ok = ok && load_key(&group, &point, ctx->public_key) &&
    mbedtls_mpi_read_binary(&r, signature, 32) == 0 && mbedtls_mpi_read_binary(&s, signature + 32, 32) == 0 &&
    mbedtls_ecdsa_verify(&group, digest, sizeof(digest), &point, &r, &s) == 0;
  mbedtls_mpi_free(&s);
  mbedtls_mpi_free(&r);
  mbedtls_ecp_point_free(&point);
  mbedtls_ecp_group_free(&group);
  mbedtls_platform_zeroize(digest, sizeof(digest));
  return ok;
}

bool vgw_crypto_hash_start(void *context) {
  vgw_crypto *ctx = context;
  if (!ctx || !ctx->ready || faulted) return false;
  mbedtls_sha256_free(&ctx->stream);
  mbedtls_sha256_init(&ctx->stream);
  ctx->streaming = mbedtls_sha256_starts(&ctx->stream, 0) == 0;
  return ctx->streaming;
}

bool vgw_crypto_hash_add(void *context, const uint8_t *data, size_t length) {
  vgw_crypto *ctx = context;
  if (!ctx) return false;
  if (!ctx->ready || faulted || !ctx->streaming || !data || !length || length > 256 ||
      mbedtls_sha256_update(&ctx->stream, data, length) != 0) {
    ctx->streaming = false;
    return false;
  }
  return true;
}

bool vgw_crypto_hash_finish(void *context, uint8_t out[32]) {
  vgw_crypto *ctx = context;
  if (out) memset(out, 0, 32);
  if (!ctx) return false;
  bool ok = out && ctx->ready && !faulted && ctx->streaming && mbedtls_sha256_finish(&ctx->stream, out) == 0;
  ctx->streaming = false;
  mbedtls_sha256_free(&ctx->stream);
  return ok;
}

bool vgw_crypto_hmac(const uint8_t key[32], const uint8_t *data, size_t length, uint8_t out[32]) {
  if (!out) return false;
  memset(out, 0, 32);
  if (faulted || !key || (!data && length) || length > 512) return false;
  setup();
  return mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, 32, data, length, out) == 0;
}
