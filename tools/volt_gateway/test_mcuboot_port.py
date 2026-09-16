"""Real upstream C direct-XIP/revert against memory flash, NOT hardware tests."""
import ctypes as C
import hashlib
import os
from pathlib import Path
import struct

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
import pytest

from openpilot.tools.volt_gateway import mcuboot_port, target_crypto

SIZE, SLOT_SIZE = 0x180000, 0xa0000
SLOTS = (0x40000, 0xe0000)
TARGET = b'test-device!' + hashlib.sha256(b'candidate-layout-only').digest()
MAGIC = bytes.fromhex('77c295f360d2ef7f3552500f2cb67980')


def image(key, slot, version, *, confirmed=False, target=TARGET, stack=0x20020000, reset=None, probe=False):
  """Ephemeral fixture only; never a deployable or production-signed image."""
  start = 0x08000000 + SLOTS[slot] + 512
  payload = struct.pack('<II', stack, start + 9 if reset is None else reset) + b'\x00\xbf' * 124
  if probe:
    # Thumb leaf: movs r0,#(0xa0+slot); ldr r1,literal; str r0,[r1]; bx lr.
    code = struct.pack('<HHHHI', 0x20a0 + slot, 0x4901, 0x6008, 0x4770, 0x20017000)
    payload = (payload[:8] + code).ljust(256, b'\x00')
  protected = struct.pack('<HHHH', 0x6908, 8 + len(target), 0xa0, len(target)) + target
  header = struct.pack('<IIHHIIBBHII', 0x96f3b83d, SLOTS[slot], 512, len(protected), len(payload),
                       0x100, version, 0, 0, 0, 0).ljust(512, b'\x00')
  content = header + payload + protected
  digest = SHA256.new(content)
  public = key.public_key().export_key(format='DER')
  signature = DSS.new(key, 'deterministic-rfc6979', encoding='der').sign(digest)
  records = b''.join(struct.pack('<HH', kind, len(data)) + data for kind, data in (
    (0x10, digest.digest()), (0x01, hashlib.sha256(public).digest()), (0x22, signature)))
  result = bytearray((content + struct.pack('<HH', 0x6907, len(records) + 4) + records).ljust(SLOT_SIZE, b'\xff'))
  result[-16:] = MAGIC
  if confirmed:
    result[-32] = result[-24] = 1
  return bytes(result)


@pytest.fixture(scope='module')
def library(tmp_path_factory):
  checkout = os.environ.get('VOLTGW_MCUBOOT_CHECKOUT')
  if not checkout or not target_crypto.archive_path().is_file():
    pytest.skip('pinned MCUboot checkout/crypto archive absent; direct-XIP C gate NOT passed')
  binary = mcuboot_port.build(Path(checkout), target_crypto.archive_path(), tmp_path_factory.mktemp('boot') / 'build')
  lib = C.CDLL(str(binary))
  p, u = C.c_void_p, C.c_uint32
  for name, result, args in (
    ('vgw_test_setup', C.c_int, [p, p, p, u]), ('vgw_test_boot', C.c_int, []),
    ('vgw_test_confirm', C.c_int, []), ('vgw_test_copy', C.c_int, [p, u]),
    ('vgw_test_fault', None, [u, u, u]), ('vgw_test_mutations', u, []),
    ('vgw_boot_get_status', None, [p]),
  ):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
  return lib


class Flash:
  def __init__(self, lib, key, a=None, b=None):
    self.lib = lib
    self.initial = bytes([0x5a]) * SLOTS[0] + (a or b'\xff' * SLOT_SIZE) + (b or b'\xff' * SLOT_SIZE)
    assert len(self.initial) == SIZE
    assert lib.vgw_test_setup(key.public_key().export_key(format='DER'), TARGET, self.initial, SIZE) == 0

  def snapshot(self):
    out = C.create_string_buffer(SIZE)
    assert self.lib.vgw_test_copy(out, SIZE) == 0
    assert out.raw[:SLOTS[0]] == self.initial[:SLOTS[0]]  # loader/provisioning never touched
    return out.raw

  def fault(self, index=0, kind=0, read=0):
    self.lib.vgw_test_fault(index, kind, read)


@pytest.fixture(scope='module')
def key():
  return ECC.generate(curve='P-256')


def test_trial_then_revert(library, key):
  a = image(key, 0, 1, confirmed=True)
  flash = Flash(library, key, a, image(key, 1, 2))
  assert library.vgw_test_boot() == 1
  status = C.create_string_buffer(3)
  library.vgw_boot_get_status(status)
  assert status.raw == bytes([2, 1, 0])
  assert flash.snapshot()[SLOTS[1] + SLOT_SIZE - 32] == 1
  assert library.vgw_test_boot() == 0
  library.vgw_boot_get_status(status)
  assert status.raw == bytes([1, 0, 0])
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a
  assert flash.snapshot()[SLOTS[1]:] == b'\xff' * SLOT_SIZE


@pytest.mark.parametrize('slot', [0, 1])
def test_confirm_persists(library, key, slot):
  images = [image(key, i, 2 if i == slot else 1, confirmed=i != slot) for i in range(2)]
  flash = Flash(library, key, *images)
  assert library.vgw_test_boot() == slot
  assert library.vgw_test_confirm() == 1
  assert library.vgw_test_confirm() == 1
  for _ in range(3):
    assert library.vgw_test_boot() == slot
  flash.snapshot()


@pytest.mark.parametrize('kind', [1, 2, 3, 4, 5])
def test_trial_write_fault_never_selects(library, key, kind):
  a = image(key, 0, 1, confirmed=True)
  flash = Flash(library, key, a, image(key, 1, 2))
  flash.fault(1, kind)
  assert library.vgw_test_boot() == -2
  assert library.vgw_test_confirm() == 0
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a
  flash.fault()
  assert library.vgw_test_boot() in (0, 1)  # before-write or false-success may retry the trial
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a


@pytest.mark.parametrize('kind', [1, 2, 3, 4, 5])
@pytest.mark.parametrize('sector', range(1, 6))
def test_revert_erase_fault(library, key, kind, sector):
  a = image(key, 0, 1, confirmed=True)
  # Populate entire invalid slot so false-success erases cannot pass vacuously.
  b = bytearray(image(key, 1, 2))
  b[4096:-64] = b'\x66' * (SLOT_SIZE - 4160)
  flash = Flash(library, key, a, bytes(b))
  assert library.vgw_test_boot() == 1
  flash.fault(sector, kind)
  assert library.vgw_test_boot() == -2
  assert library.vgw_test_confirm() == 0
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a
  flash.fault()
  assert library.vgw_test_boot() == 0


@pytest.mark.parametrize('kind', [1, 2, 3, 4, 5])
def test_confirmation_fault(library, key, kind):
  flash = Flash(library, key, image(key, 0, 1, confirmed=True), image(key, 1, 2))
  assert library.vgw_test_boot() == 1
  flash.fault(1, kind)
  assert library.vgw_test_confirm() == 0
  if kind not in (4, 5):  # power loss ends execution, not a returning API error
    assert library.vgw_test_confirm() == 0
  flash.fault()
  # A torn write can finish the one-way confirmation bit before reporting an
  # error. Either signed image is safe; claiming every failed call reverts isn't.
  assert library.vgw_test_boot() in (0, 1)
  flash.snapshot()


@pytest.mark.parametrize('read', [1, 2, 3, 4, 5, 10, 20])
def test_read_failure(library, key, read):
  flash = Flash(library, key, image(key, 0, 1, confirmed=True), image(key, 1, 2))
  flash.fault(read=read)
  assert library.vgw_test_boot() == -2
  assert library.vgw_test_confirm() == 0
  flash.snapshot()


@pytest.mark.parametrize('defect', ['signature', 'bad_signature', 'payload', 'target', 'stack', 'reset', 'wrong_slot', 'missing_binding'])
def test_reject_bad_image(library, key, defect):
  a = image(key, 0, 1, confirmed=True)
  kwargs = {}
  if defect == 'target':
    kwargs['target'] = b'X' * 44
  if defect == 'missing_binding':
    kwargs['target'] = b''
  if defect == 'stack':
    kwargs['stack'] = 0x30020000
  if defect == 'reset':
    kwargs['reset'] = 0x08000001
  signing = ECC.generate(curve='P-256') if defect == 'signature' else key
  b = bytearray(image(signing, 0 if defect == 'wrong_slot' else 1, 2, **kwargs))
  if defect == 'payload':
    b[550] ^= 1
  if defect == 'bad_signature':
    # Last DER signature byte, keeping the correct public-key hash and TLVs.
    tlv_start = 512 + 256 + 52
    total = struct.unpack_from('<H', b, tlv_start + 2)[0]
    b[tlv_start + total - 1] ^= 1
  flash = Flash(library, key, a, bytes(b))
  assert library.vgw_test_boot() in (0, -3)
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a


def test_both_invalid_no_boot(library, key):
  flash = Flash(library, key)
  assert library.vgw_test_boot() == -1
  status = C.create_string_buffer(3)
  library.vgw_boot_get_status(status)
  assert status.raw == bytes([5, 255, 1])
  assert library.vgw_test_confirm() == 0
  assert flash.snapshot() == flash.initial


@pytest.mark.parametrize('offset', range(0, 32, 2))
def test_corrupted_header_never_boots_trial(library, key, offset):
  a = image(key, 0, 1, confirmed=True)
  b = bytearray(image(key, 1, 2))
  b[offset] ^= 0x80
  flash = Flash(library, key, a, bytes(b))
  assert library.vgw_test_boot() in (-3, -2, -1, 0)
  assert flash.snapshot()[SLOTS[0]:SLOTS[1]] == a
