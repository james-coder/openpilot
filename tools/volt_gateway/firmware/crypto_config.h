#ifndef VOLTGW_CRYPTO_CONFIG_H
#define VOLTGW_CRYPTO_CONFIG_H
/* Deliberately no TLS, certificates, PEM, filesystem, sockets, signing or
 * entropy fallback. Only P-256 verification, SHA256 and HMAC are exposed by
 * our adapter. Mbed TLS's private-key routines are removed by section GC. */
#define MBEDTLS_BIGNUM_C
#define MBEDTLS_ECP_C
#define MBEDTLS_ECP_RESTARTABLE
#define MBEDTLS_ECP_DP_SECP256R1_ENABLED
#define MBEDTLS_ECP_NIST_OPTIM
#define MBEDTLS_ECDSA_C
#define MBEDTLS_ASN1_PARSE_C
#define MBEDTLS_ASN1_WRITE_C
#define MBEDTLS_SHA256_C
#define MBEDTLS_MD_C
#define MBEDTLS_PLATFORM_C
#define MBEDTLS_PLATFORM_MEMORY
#define MBEDTLS_PLATFORM_NO_STD_FUNCTIONS
#define MBEDTLS_MEMORY_BUFFER_ALLOC_C
#define MBEDTLS_MPI_MAX_SIZE 32
#define MBEDTLS_HAVE_INT32
/* Use upstream's 32-bit division implementation; the small ARM toolchain
 * available here does not ship a libgcc 64-bit division runtime. */
#define MBEDTLS_NO_UDBL_DIVISION
#define MBEDTLS_ECP_WINDOW_SIZE 2
#define MBEDTLS_ECP_FIXED_POINT_OPTIM 0
#define MBEDTLS_PLATFORM_STD_EXIT_FAILURE 1
#define MBEDTLS_PLATFORM_EXIT_MACRO vgw_crypto_panic
_Noreturn void vgw_crypto_panic(int status);
#endif
