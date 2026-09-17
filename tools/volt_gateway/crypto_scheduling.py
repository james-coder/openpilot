"""Exact, fail-closed scheduling patch to the pinned verification-only backend.

No arithmetic changes. Mbed TLS's restart budget does not subdivide its
constant-iteration modular inverse. Yield at public loop-index boundaries.
Only full board builds use this patch; upstream source remains hash-pinned.
"""
import hashlib


def patch(root):
  path = root / 'library/bignum_core.c'
  original = path.read_text()
  anchor = '    for (size_t i = 0; i < (A_limbs + N_limbs) * biL; i++) {\n'
  if original.count(anchor) != 1 or 'vgw_crypto_service_slice' in original:
    raise ValueError('pinned modular-inverse scheduling anchor changed')
  updated = original.replace(anchor, anchor +
    '        /* Volt gateway: no arithmetic or loop-bound changes. */\n' +
    '        if ((i & 7U) == 0) {\n' +
    '            extern void vgw_crypto_service_slice(void);\n' +
    '            vgw_crypto_service_slice();\n' +
    '        }\n')
  path.write_text(updated)
  return {'file': 'library/bignum_core.c', 'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
          'after_sha256': hashlib.sha256(updated.encode()).hexdigest(), 'iteration_stride': 8}
