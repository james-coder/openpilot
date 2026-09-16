"""Framed update -> C updater -> persistent model -> reset/boot/confirm/revert.

Boot selection is a Python model, NOT production MCUboot or executed app code.
"""

from dataclasses import replace
import hashlib
import struct

import pytest

from openpilot.tools.volt_gateway.authority import AuthorityError, Challenge
from openpilot.tools.volt_gateway.boot_model import BootModel, InactiveSlot, PowerCut, Storage
from openpilot.tools.volt_gateway.native_harness import compile_authority
from openpilot.tools.volt_gateway.native_update import NativeUpdateEngine
from openpilot.tools.volt_gateway.operator import authorize_image, sign_image
from openpilot.tools.volt_gateway.test_system import setup


@pytest.fixture(scope='module')
def library(tmp_path_factory):
  path = tmp_path_factory.mktemp('lifecycle') / 'update.so'
  compile_authority(path)
  return path


def lifecycle(library):
  owner, raw, signed, _, gateway, relay = setup(library)
  storage = Storage()
  old = b'previous confirmed application' * 20
  previous = sign_image(owner, replace(signed.manifest, size=len(old), digest=hashlib.sha256(old).digest(), version=1))
  storage.slots[0][:len(old)] = old
  storage.headers[0] = previous.pack()
  storage.persist(0)
  storage.mutations = 0
  slot = InactiveSlot(storage, signed)
  gateway.engine = NativeUpdateEngine(gateway.authority, slot, gateway.environment)

  def reset():
    return BootModel(storage, owner.public_key().export_key(format='DER'), signed.manifest.device, signed.manifest.layout)

  def install(fault=None):
    challenge = Challenge.unpack(relay.call(0x40, signed.pack(), fault))
    relay.call(0x41, authorize_image(owner, signed, challenge, 60000).pack(), fault)
    for offset in range(0, len(raw), 256):
      relay.call(0x42, struct.pack('!I', offset) + raw[offset:offset+256], fault)
    relay.call(0x43, b'', 'lost_ack')

  return storage, reset, install, gateway


@pytest.mark.parametrize('fault', [None, 'lost_ack', 'corrupt', 'missing', 'duplicate', 'reorder', 'delay', 'burst_loss', 'jitter'])
@pytest.mark.parametrize('confirm', [False, True])
def test_install_reset_confirm_or_revert(library, fault, confirm):
  storage, reset, install, _ = lifecycle(library)
  before = hashlib.sha256(storage.slots[0]).digest()
  install(fault)
  loader = reset()
  assert loader.boot() == 1
  if confirm:
    loader.confirm()
  assert reset().boot() == (1 if confirm else 0)
  assert hashlib.sha256(storage.slots[0]).digest() == before


@pytest.mark.parametrize('cut_after', range(81))
def test_cut_each_update_persistent_mutation(library, cut_after):
  storage, reset, install, _ = lifecycle(library)
  before = hashlib.sha256(storage.slots[0]).digest()
  storage.cut_after = cut_after
  try:
    install()
  except AuthorityError:
    # ctypes adapter converts simulated storage failure into an explicit NACK.
    assert storage.mutations == cut_after
  storage.cut_after = None
  assert hashlib.sha256(storage.slots[0]).digest() == before
  loader = reset()
  selected = loader.boot()
  assert selected in (0, 1)
  assert loader.valid(selected)
  # A trial that never confirms must not displace the old confirmed image.
  assert reset().boot() == 0


@pytest.mark.parametrize('phase', ['attempt', 'confirm', 'revert'])
@pytest.mark.parametrize('cut_after', range(7))
def test_journal_interruption_during_boot_transitions(library, phase, cut_after):
  storage, reset, install, _ = lifecycle(library)
  install()
  loader = reset()
  if phase != 'attempt':
    assert loader.boot() == 1
  storage.mutations = 0
  storage.cut_after = cut_after
  try:
    if phase == 'confirm':
      loader.confirm()
    else:
      reset().boot()
  except PowerCut:
    pass
  storage.cut_after = None
  rebooted = reset()
  selected = rebooted.boot()
  assert selected in (0, 1) and rebooted.valid(selected)
  if phase == 'revert':
    assert selected == 0


@pytest.mark.parametrize('damage', ['image', 'header', 'both_images', 'both_metadata'])
def test_corruption_never_selects_invalid_app(library, damage):
  storage, reset, install, _ = lifecycle(library)
  install()
  if damage in ('image', 'both_images'):
    storage.slots[1][0] ^= 1
  elif damage == 'header':
    storage.headers[1] = storage.headers[1][:-1] + bytes([storage.headers[1][-1] ^ 1])
  if damage == 'both_images':
    storage.slots[0][0] ^= 1
  elif damage == 'both_metadata':
    for page in storage.journal:
      page[0] ^= 1
  assert reset().boot() == ('recovery' if damage in ('both_images', 'both_metadata') else 0)


def test_metadata_generation_wrap_rejected():
  from openpilot.tools.volt_gateway.boot_model import COMMIT, RECORD
  import zlib
  storage = Storage()
  record = RECORD.pack(b'GWJR', 0xffffffff, 0, 255, 0)
  encoded = record + struct.pack('!I', zlib.crc32(record)) + COMMIT
  storage.journal[0][:len(encoded)] = encoded
  with pytest.raises(AuthorityError):
    storage.persist(0)
