#include "crypto.h"
#include "mbedtls/ecdsa.h"
static bool (*pump)(void *);
static void *context;
static bool busy;
bool vgw_crypto_cooperative_init(bool (*service)(void *),void *ctx) {
  if (!service || !ctx || busy || pump) return false;
  pump=service; context=ctx;
  mbedtls_ecp_set_max_ops(1); /* smallest upstream restart budget; not a WCET claim */
  return true;
}
int __wrap_mbedtls_ecdsa_verify(mbedtls_ecp_group *grp,const unsigned char *hash,size_t length,
                              const mbedtls_ecp_point *q,const mbedtls_mpi *r,const mbedtls_mpi *s) {
  if (!pump || busy) return MBEDTLS_ERR_ECP_BAD_INPUT_DATA;
  busy=true;
  mbedtls_ecdsa_restart_ctx restart;
  mbedtls_ecdsa_restart_init(&restart);
  int result=MBEDTLS_ERR_ECP_IN_PROGRESS;
  for (unsigned i=0;i<65536U && result==MBEDTLS_ERR_ECP_IN_PROGRESS;i++) {
    if (!pump(context)) { result=MBEDTLS_ERR_ECP_BAD_INPUT_DATA; break; }
    result=mbedtls_ecdsa_verify_restartable(grp,hash,length,q,r,s,&restart);
  }
  if (result==0 && !pump(context)) result=MBEDTLS_ERR_ECP_BAD_INPUT_DATA;
  mbedtls_ecdsa_restart_free(&restart);
  busy=false;
  return result;
}
int __wrap_mbedtls_ecdsa_read_signature(mbedtls_ecdsa_context *ctx,const unsigned char *hash,size_t length,
                                      const unsigned char *signature,size_t size) {
  if (!pump || busy) return MBEDTLS_ERR_ECP_BAD_INPUT_DATA;
  busy=true;
  mbedtls_ecdsa_restart_ctx restart;
  mbedtls_ecdsa_restart_init(&restart);
  int result=MBEDTLS_ERR_ECP_IN_PROGRESS;
  for (unsigned i=0;i<65536U && result==MBEDTLS_ERR_ECP_IN_PROGRESS;i++) {
    if (!pump(context)) { result=MBEDTLS_ERR_ECP_BAD_INPUT_DATA; break; }
    result=mbedtls_ecdsa_read_signature_restartable(ctx,hash,length,signature,size,&restart);
  }
  if (result==0 && !pump(context)) result=MBEDTLS_ERR_ECP_BAD_INPUT_DATA;
  mbedtls_ecdsa_restart_free(&restart);
  busy=false;
  return result;
}
