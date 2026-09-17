#ifndef VGW_RECOVERY_SERVICE_H
#define VGW_RECOVERY_SERVICE_H
#include "update.h"
#define VGW_RECOVERY_HELLO_SIZE 147U
/* Crypto and entropy callbacks are fixed platform bindings, never wire fields.
 * Pairing secret authorizes the transport; it CANNOT grant PROGRAM authority.
 * Every flash operation also requires a fresh independently signed operator
 * grant for the exact signed image, enforced by authority.c/update.c. */
typedef bool (*vgw_hmac_fn)(const uint8_t[32],const uint8_t *,size_t,uint8_t[32]);
typedef bool (*vgw_hash_fn)(const uint8_t *,size_t,uint8_t[32]);
typedef bool (*vgw_nonce_fn)(void *,uint8_t[32]);
/* Fixed application binding, never supplied over CAN. Called only after a
 * complete authenticated, sequenced command. reset removes session permissions. */
typedef size_t (*vgw_command_fn)(void *,uint8_t,const uint8_t *,size_t,uint64_t,uint8_t[128]);
typedef struct {
  uint8_t pairing[32], device[12], layout[32], policy[32], build[32];
} vgw_recovery_provision;
typedef struct {
  vgw_recovery_provision provision;
  vgw_authority authority;
  vgw_update update;
  vgw_update_io storage;
  vgw_verify_fn verify;
  vgw_sha256_fn authority_hash;
  vgw_hmac_fn hmac;
  vgw_hash_fn hash;
  vgw_nonce_fn nonce;
  void *crypto_context,*nonce_context;
  uint8_t hello[VGW_RECOVERY_HELLO_SIZE], host_key[32], gateway_key[32], session[8];
  uint8_t last_request[512], last_reply[512], opened[69], open_reply[56];
  uint16_t request_size,reply_size;
  uint8_t target_slot;
  uint64_t previous,expires,next_sequence,last_public,last_open,last_authenticated;
  bool ready,fresh,active,public_seen,open_seen;
  vgw_command_fn application;
  void (*application_reset)(void *);
  void *application_context;
} vgw_recovery_service;
size_t vgw_recovery_service_size(void);
bool vgw_recovery_service_init(vgw_recovery_service *,const vgw_recovery_provision *,const vgw_update_io *,
  vgw_verify_fn,vgw_sha256_fn,vgw_hmac_fn,vgw_hash_fn,vgw_nonce_fn,void *crypto,void *entropy,unsigned inactive_slot);
/* Complete PDU only. Out is 512 bytes, disjoint from input. A false result
 * means silent rejection; never send leftover output. No partial command
 * acts. Identical last-request retransmission returns the cached response.
 * Must be serialized with storage/crypto ownership; not ISR/flash-busy safe. */
bool vgw_recovery_service_request(vgw_recovery_service *,const uint8_t *,size_t,uint64_t now,uint8_t out[512],size_t *out_size);
bool vgw_recovery_service_tick(vgw_recovery_service *,uint64_t now);
void vgw_recovery_service_close(vgw_recovery_service *);
bool vgw_recovery_service_application(vgw_recovery_service *,vgw_command_fn,void (*)(void *),void *);
#endif
