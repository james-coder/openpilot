"""Execute the portable C gate with real host crypto; NOT on-device crypto tests."""

import ctypes as c
import hashlib
from pathlib import Path
import subprocess

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.authority import (
  AUTH_DOMAIN, IMAGE_DOMAIN, AuthorityError, Challenge, ImageManifest, Phase, verify,
)
from openpilot.tools.volt_gateway.operator import authorize_image, sign_image


VERIFY = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_bool, c.c_void_p, c.c_size_t, c.c_void_p)
HASH = c.CFUNCTYPE(None, c.c_void_p, c.c_size_t, c.c_void_p)


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
  directory = Path(__file__).parent / 'firmware'
  output = tmp_path_factory.mktemp('voltgw-c') / 'authority.so'
  subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-shared', '-fPIC',
                  str(directory / 'authority.c'), '-o', str(output)], check=True)
  lib = c.CDLL(str(output))
  lib.vgw_authority_size.restype = c.c_size_t
  lib.vgw_authority_init.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_uint8, c.c_void_p, VERIFY, HASH, c.c_void_p]
  lib.vgw_authority_init.restype = c.c_bool
  lib.vgw_authority_challenge.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64, c.c_void_p, c.c_void_p]
  lib.vgw_authority_challenge.restype = c.c_bool
  lib.vgw_authority_accept.argtypes = [c.c_void_p, c.c_void_p, c.c_size_t, c.c_uint64]
  lib.vgw_authority_accept.restype = c.c_bool
  lib.vgw_authority_require.argtypes = [c.c_void_p, c.c_uint8, c.c_uint64]
  lib.vgw_authority_require.restype = c.c_bool
  return lib


class Native:
  def __init__(self, lib, phase=Phase.PROGRAM):
    self.lib, self.phase = lib, phase
    self.key = ECC.generate(curve='P-256')
    raw = b'firmware fixture'
    m = ImageManifest(b'd' * 12, 1, b'l' * 32, len(raw), hashlib.sha256(raw).digest(), b'b' * 32, 1)
    self.image = sign_image(self.key, m)
    self.state = c.create_string_buffer(lib.vgw_authority_size())
    self.verifications = 0

    def check(_, image, data, size, signature):
      self.verifications += 1
      try:
        verify(self.key.public_key(), IMAGE_DOMAIN if image else AUTH_DOMAIN, c.string_at(data, size), c.string_at(signature, 64))
        return True
      except AuthorityError:
        return False

    def sha(data, size, out):
      c.memmove(out, hashlib.sha256(c.string_at(data, size)).digest(), 32)

    self.verify, self.hash = VERIFY(check), HASH(sha)
    assert lib.vgw_authority_init(self.state, m.device, m.layout, phase, b's' * 32, self.verify, self.hash, None)

  def challenge(self, now=0, nonce=b'n' * 32, image=None):
    image = self.image.pack() if image is None else image
    out = c.create_string_buffer(82)
    ok = self.lib.vgw_authority_challenge(self.state, image, len(image), now, nonce, out)
    return Challenge.unpack(out.raw) if ok else None

  def accept(self, token, now=0):
    return self.lib.vgw_authority_accept(self.state, token, len(token), now)

  def require(self, now=0, phase=Phase.PROGRAM):
    return self.lib.vgw_authority_require(self.state, phase, now)


def test_native_python_wire_compatibility(lib):
  n = Native(lib)
  assert not n.require()
  challenge = n.challenge()
  assert challenge == Challenge(Phase.PROGRAM, b'd' * 12, b's' * 32, b'n' * 32)
  token = authorize_image(n.key, n.image, challenge, 1000).pack()
  assert n.accept(token)
  assert n.require(1)
  assert not n.require(1, Phase.ENTER)
  assert not n.accept(token, 1)
  assert not n.require(1000)
  assert not n.require(999)


@pytest.mark.parametrize('index', [0, 4, 5, 6, 17, 18, 49, 50, 81, 82, 113, 114, 117, 118, 181])
def test_native_modified_authorization(lib, index):
  n = Native(lib)
  token = bytearray(authorize_image(n.key, n.image, n.challenge(), 60000).pack())
  token[index] ^= 1
  assert not n.accept(bytes(token))
  assert not n.require(1)


def test_native_rejects_app_token_loader_expiry_and_unknown_size(lib):
  n = Native(lib, Phase.ENTER)
  token = authorize_image(n.key, n.image, n.challenge(), 60000).pack()
  assert n.accept(token)
  assert not n.require(1, Phase.PROGRAM)
  assert n.require(1, Phase.ENTER)
  for raw in (b'', bytes(181), bytes(183), bytes(512)):
    assert not n.accept(raw, 2)
  n = Native(lib)
  token = authorize_image(n.key, n.image, n.challenge(), 60000).pack()
  assert not n.accept(token, 60000)


def test_native_entropy_and_flood_limits(lib):
  n = Native(lib)
  assert n.challenge(nonce=bytes(32)) is None
  assert n.challenge(now=60000) is None  # entropy failure closes the entire context
  n = Native(lib)
  challenge = n.challenge()
  for t in range(1, 100):
    assert n.challenge(t) == challenge
  assert n.verifications == 1
  token = authorize_image(n.key, n.image, challenge, 60000).pack()
  bad = token[:-64] + bytes(64)
  for t in range(100, 200):
    assert not n.accept(bad, t)
  assert n.verifications == 2  # manifest + one bad authorization, not 101
  assert n.accept(token, 1100)


def test_native_boundary_clock_and_manifest(lib):
  n = Native(lib)
  assert n.challenge(image=bytes(186)) is None
  assert n.challenge(1) is None
  assert n.challenge(60000) is not None
  assert not n.require(59999)
  assert n.challenge(60001) is None
