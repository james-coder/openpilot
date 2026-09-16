import ctypes as C
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys

from Crypto.PublicKey import ECC
from Crypto.Hash import SHA256
from Crypto.Signature import DSS
import pytest

from openpilot.tools.volt_gateway import m4_emulation, target_crypto
from openpilot.tools.volt_gateway.authority import AUTH_DOMAIN, IMAGE_DOMAIN, Challenge, ImageManifest, Phase
from openpilot.tools.volt_gateway.operator import authorize_image, sign_image
from openpilot.tools.volt_gateway.native_harness import NativeLibraries, compile_authority
from openpilot.tools.volt_gateway import test_lifecycle as lifecycle_tests, test_system as system_tests


@pytest.fixture(scope='module')
def archive():
  path = target_crypto.archive_path()
  if not path.is_file():
    pytest.skip('pinned Mbed TLS archive absent; target crypto gate NOT passed; set VOLTGW_MBEDTLS_ARCHIVE')
  return path


@pytest.fixture(scope='module')
def native(archive, tmp_path_factory):
  path = target_crypto.build(archive, tmp_path_factory.mktemp('native-crypto') / 'build', native=True)
  lib = C.CDLL(str(path))
  p, n, b = C.c_void_p, C.c_size_t, C.c_bool
  for name, result, args in (
    ('vgw_crypto_size', n, []), ('vgw_crypto_init', b, [p, p, n]), ('vgw_crypto_free', None, [p]),
    ('vgw_crypto_verify', b, [p, b, p, n, p]), ('vgw_crypto_sha256', b, [p, n, p]),
    ('vgw_crypto_authority_hash', None, [p, n, p]), ('vgw_crypto_hash_start', b, [p]),
    ('vgw_crypto_hash_add', b, [p, p, n]), ('vgw_crypto_hash_finish', b, [p, p]),
    ('vgw_crypto_hmac', b, [p, p, n, p]), ('mbedtls_calloc', p, [n, n]), ('mbedtls_free', None, [p]),
  ):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
  return lib


def sec1(key):
  return b'\x04' + int(key.pointQ.x).to_bytes(32, 'big') + int(key.pointQ.y).to_bytes(32, 'big')


@pytest.fixture
def crypto(native):
  # Ephemeral test key only, never a provisioned production key.
  key = ECC.generate(curve='P-256')
  ctx = C.create_string_buffer(native.vgw_crypto_size())
  public = sec1(key)
  assert native.vgw_crypto_init(ctx, public, len(public))
  yield ctx, key
  native.vgw_crypto_free(ctx)


@pytest.mark.parametrize('length', [0, 1, 31, 55, 56, 63, 64, 65, 255, 256, 511, 512, 4096])
def test_sha_and_bounded_hmac(native, length):
  data = bytes(i % 251 for i in range(length))
  out = C.create_string_buffer(32)
  assert native.vgw_crypto_sha256(data, length, out)
  assert out.raw == hashlib.sha256(data).digest()
  accepted = native.vgw_crypto_hmac(b'k'*32, data, length, out)
  assert accepted == (length <= 512)
  assert out.raw == (hmac.digest(b'k'*32, data, 'sha256') if accepted else bytes(32))


@pytest.mark.parametrize('image', [True, False])
def test_signatures_and_rejection(native, crypto, image):
  ctx, key = crypto
  data = b'a' * (122 if image else 118)
  domain = IMAGE_DOMAIN if image else AUTH_DOMAIN
  signature = DSS.new(key, 'fips-186-3', encoding='binary').sign(SHA256.new(domain + data))
  assert native.vgw_crypto_verify(ctx, image, data, len(data), signature)
  for damaged in (bytes(64), b'\xff'*64, signature[:32] + bytes(32), bytes(32) + signature[32:],
                  signature[:-1] + bytes([signature[-1] ^ 1])):
    assert not native.vgw_crypto_verify(ctx, image, data, len(data), damaged)
  wrong_domain = DSS.new(key, 'fips-186-3', encoding='binary').sign(SHA256.new(b'other\0' + data))
  assert not native.vgw_crypto_verify(ctx, image, data, len(data), wrong_domain)
  assert not native.vgw_crypto_verify(ctx, image, b'b' + data[1:], len(data), signature)
  assert not native.vgw_crypto_verify(ctx, image, data, len(data)-1, signature)
  assert not native.vgw_crypto_verify(ctx, not image, data, len(data), signature)
  assert not native.vgw_crypto_verify(ctx, image, None, len(data), signature)
  assert not native.vgw_crypto_verify(ctx, image, data, len(data), None)
  # Repetition exercises bounded allocator reuse/leak behavior.
  for _ in range(25):
    assert native.vgw_crypto_verify(ctx, image, data, len(data), signature)


@pytest.mark.parametrize('key', [b'', bytes(65), b'\x04' + bytes(64), b'\x04' + b'\xff'*64, b'\x02' + bytes(32)])
def test_bad_public_key(native, crypto, key):
  ctx, _ = crypto
  assert not native.vgw_crypto_init(ctx, key, len(key))
  assert not native.vgw_crypto_verify(ctx, True, b'a'*122, 122, bytes(64))
  assert not native.vgw_crypto_hash_start(ctx)


def test_stream_lifecycle(native, crypto):
  ctx, _ = crypto
  out = C.create_string_buffer(32)
  assert not native.vgw_crypto_hash_add(ctx, b'a', 1)
  assert not native.vgw_crypto_hash_finish(ctx, out)
  assert native.vgw_crypto_hash_start(ctx)
  for chunk in (b'a'*256, b'b'*256, b'c'):
    assert native.vgw_crypto_hash_add(ctx, chunk, len(chunk))
  assert native.vgw_crypto_hash_finish(ctx, out)
  assert out.raw == hashlib.sha256(b'a'*256 + b'b'*256 + b'c').digest()
  assert not native.vgw_crypto_hash_finish(ctx, out)
  assert out.raw == bytes(32)
  assert native.vgw_crypto_hash_start(ctx)
  assert not native.vgw_crypto_hash_add(ctx, b'a'*257, 257)
  assert not native.vgw_crypto_hash_finish(ctx, out)
  assert native.vgw_crypto_hash_start(ctx)
  assert native.vgw_crypto_hash_finish(ctx, out)
  assert out.raw == hashlib.sha256(b'').digest()


def test_allocator_exhaustion_rejects_without_fallback(native, crypto):
  ctx, key = crypto
  body = b'a'*122
  signature = DSS.new(key, 'fips-186-3', encoding='binary').sign(SHA256.new(IMAGE_DOMAIN + body))
  allocations = []
  try:
    for _ in range(128):
      ptr = native.mbedtls_calloc(1, 256)
      if not ptr:
        break
      allocations.append(ptr)
    else:
      pytest.fail('allocator escaped fixed arena')
    assert allocations
    assert not native.vgw_crypto_verify(ctx, True, body, len(body), signature)
  finally:
    for ptr in allocations:
      native.mbedtls_free(ptr)
  assert native.vgw_crypto_verify(ctx, True, body, len(body), signature)


def test_internal_hash_error_latches_closed(native):
  # A fresh process avoids leaking deliberately latched failure to other tests.
  code = '''
import ctypes as C, sys
l = C.CDLL(sys.argv[1])
l.vgw_crypto_authority_hash.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
l.vgw_crypto_sha256.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
l.vgw_crypto_sha256.restype = C.c_bool
out = C.create_string_buffer(32)
l.vgw_crypto_authority_hash(None, 1, out)
assert not l.vgw_crypto_sha256(b"abc", 3, out)
assert out.raw == bytes(32)
'''
  subprocess.run([sys.executable, '-c', code, native._name], check=True, timeout=10)


@pytest.fixture(scope='module')
def arm(archive, tmp_path_factory):
  pytest.importorskip('unicorn', reason='target crypto CPU test is not available')
  return target_crypto.build(archive, tmp_path_factory.mktemp('arm-crypto') / 'build')


@pytest.mark.parametrize('damage', ['none', 'token', 'image', 'wrong-key'])
def test_arm_authority_and_update_without_host_crypto(arm, damage):
  key = ECC.generate(curve='P-256')
  raw = b'public emulator image fixture'
  image = sign_image(key, ImageManifest(b'd'*12, 1, b'l'*32, len(raw), hashlib.sha256(raw).digest(), b'b'*32, 1))
  token = authorize_image(key, image, Challenge(Phase.PROGRAM, b'd'*12, b's'*32, b'n'*32), 60000).pack()
  signed = image.pack()
  if damage == 'token':
    token = token[:-1] + bytes([token[-1] ^ 1])
  elif damage == 'image':
    signed = signed[:-1] + bytes([signed[-1] ^ 1])
  elif damage == 'wrong-key':
    key = ECC.generate(curve='P-256')
  result = m4_emulation.run(arm, signed, token, key.public_key().export_key(format='DER'), target_crypto=True)
  assert result == {'completed': True, 'crypto_calls': 0, 'authorized': damage == 'none', 'updated': damage == 'none',
                    'failure_bits': 2 if damage in ('image', 'wrong-key') else 0}
  report = json.loads((arm.parent / 'report.json').read_text())
  assert report['undefined_symbols'] == ''
  assert not report['host_crypto_hooks'] and not report['bootable'] and not report['flashed']


def test_archive_tampering_rejected(tmp_path):
  archive = tmp_path / 'bad.tar.bz2'
  archive.write_bytes(b'not the pinned source')
  with pytest.raises(ValueError, match='checksum'):
    target_crypto.extract(archive, tmp_path / 'source')


def test_extract_uses_verified_bytes_not_reopened_path(archive, tmp_path, monkeypatch):
  original = target_crypto.tarfile.open
  calls = []

  def checked(*args, **kwargs):
    assert not args and 'fileobj' in kwargs
    assert hashlib.sha256(kwargs['fileobj'].getvalue()).hexdigest() == target_crypto.ARCHIVE_SHA256
    calls.append(True)
    return original(**kwargs)

  monkeypatch.setattr(target_crypto.tarfile, 'open', checked)
  target_crypto.extract(archive, tmp_path)
  assert calls == [True]


def test_arm_build_is_reproducible(arm, archive, tmp_path):
  second = target_crypto.build(archive, tmp_path / 'second')
  assert second.read_bytes() == arm.read_bytes()


@pytest.fixture(scope='module')
def libraries(native, tmp_path_factory):
  core = tmp_path_factory.mktemp('crypto-system') / 'core.so'
  compile_authority(core)
  return NativeLibraries(core, Path(native._name))


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'corrupt', 'missing', 'reorder', 'duplicate', 'delay', 'burst_loss', 'jitter'])
def test_update_transport_uses_c_crypto(libraries, fault):
  system_tests.test_complete_update_through_native_guard(libraries, fault)


def test_two_authorities_with_c_crypto(libraries):
  system_tests.test_compromised_relay_cannot_start_install(libraries)


@pytest.mark.parametrize('cut_after', range(81))
def test_update_interruption_with_c_crypto(libraries, cut_after):
  lifecycle_tests.test_cut_each_update_persistent_mutation(libraries, cut_after)


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'corrupt', 'missing', 'reorder', 'duplicate', 'delay', 'burst_loss', 'jitter'])
@pytest.mark.parametrize('confirm', [False, True])
def test_trial_lifecycle_with_c_crypto(libraries, fault, confirm):
  lifecycle_tests.test_install_reset_confirm_or_revert(libraries, fault, confirm)
