"""Host execution of the portable C authority, with public-only host crypto.

Test harness, not a device driver. ctypes and a host shared library do not emulate
an STM32, its crypto implementation, CAN controller, reset path or flash hardware.
"""

import ctypes as c
import hashlib
from pathlib import Path
import secrets
import subprocess

from openpilot.tools.volt_gateway.authority import AUTH_DOMAIN, IMAGE_DOMAIN, AuthorityError, Challenge, Phase, public_key, verify


VERIFY = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_bool, c.c_void_p, c.c_size_t, c.c_void_p)
HASH = c.CFUNCTYPE(None, c.c_void_p, c.c_size_t, c.c_void_p)


def compile_authority(output: Path):
  if output.exists():
    raise FileExistsError(output)
  source = Path(__file__).parent / 'firmware/authority.c'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
                  str(source), str(source.with_name('update.c')), '-o', str(output)], check=True, capture_output=True, timeout=60)


class NativeAuthority:
  def __init__(self, library: Path, verification_key: bytes, device: bytes, layout: bytes, phase: Phase):
    key = public_key(verification_key)
    if len(device) != 12 or len(layout) != 32 or phase not in (Phase.ENTER, Phase.PROGRAM):
      raise AuthorityError('native harness configuration')
    self.lib = c.CDLL(str(library))
    self.lib.vgw_authority_size.restype = c.c_size_t
    self.lib.vgw_authority_init.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint8, c.c_void_p, VERIFY, HASH, c.c_void_p]
    self.lib.vgw_authority_init.restype = c.c_bool
    self.lib.vgw_authority_close.argtypes = [c.c_void_p]
    self.lib.vgw_authority_close.restype = None
    self.lib.vgw_authority_challenge.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64, c.c_void_p, c.c_void_p]
    self.lib.vgw_authority_challenge.restype = c.c_bool
    self.lib.vgw_authority_accept.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]
    self.lib.vgw_authority_accept.restype = c.c_bool
    self.lib.vgw_authority_require.argtypes = [c.c_void_p, c.c_uint8, c.c_uint64]
    self.lib.vgw_authority_require.restype = c.c_bool
    self.phase = phase
    self.image = None
    self.state = c.create_string_buffer(self.lib.vgw_authority_size())

    def check(_, is_image, data, size, signature):
      try:
        verify(key, IMAGE_DOMAIN if is_image else AUTH_DOMAIN, c.string_at(data, size), c.string_at(signature, 64))
        return True
      except AuthorityError:
        return False

    def sha(data, size, out):
      c.memmove(out, hashlib.sha256(c.string_at(data, size)).digest(), 32)

    self._verify, self._sha = VERIFY(check), HASH(sha)
    if not self.lib.vgw_authority_init(self.state, device, layout, phase, secrets.token_bytes(32), self._verify, self._sha, None):
      raise AuthorityError('native initialization rejected')

  def _time(self, now_ms):
    if type(now_ms) is not int or not 0 <= now_ms < 2**63:
      self.close()
      raise AuthorityError('invalid native clock')

  def challenge(self, image, now_ms):
    self._time(now_ms)
    raw = image.pack()
    out = c.create_string_buffer(82)
    if not self.lib.vgw_authority_challenge(self.state, raw, len(raw), now_ms, secrets.token_bytes(32), out):
      raise AuthorityError('native challenge rejected')
    self.image = image
    return Challenge.unpack(out.raw)

  def authorize(self, token, now_ms):
    self._time(now_ms)
    raw = token.pack()
    if not self.lib.vgw_authority_accept(self.state, raw, len(raw), now_ms):
      raise AuthorityError('native authorization rejected')

  def require(self, phase, now_ms):
    self._time(now_ms)
    if not self.lib.vgw_authority_require(self.state, phase, now_ms) or self.image is None:
      raise AuthorityError('native operator authorization required')
    return self.image

  def close(self):
    self.lib.vgw_authority_close(self.state)
