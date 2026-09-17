"""C authority/updater -> real MCUboot C loader; modeled storage, no USB/CAN IO."""
import ctypes as C
import hashlib

import pytest

from openpilot.tools.volt_gateway import boot_image
from openpilot.tools.volt_gateway import test_mcuboot_port as boot
from openpilot.tools.volt_gateway import test_native_update as update
from openpilot.tools.volt_gateway.authority import AuthorityError, ImageManifest
from openpilot.tools.volt_gateway.operator import sign_image
from openpilot.tools.volt_gateway.test_boot_image import payload

library = boot.library
key = boot.key
update_library = update.library


class Storage:
  capacity = boot.SLOT_SIZE
  erase_regions = tuple((offset, 0x20000) for offset in range(0, capacity, 0x20000))

  def __init__(self, library, slot):
    self.lib, self.slot = library, slot

  def access(self, operation, offset, data, size):
    if not self.lib.vgw_test_storage(self.slot, operation, offset, data, size):
      raise OSError('simulated storage fault')

  def erase(self, offset, size):
    self.access(2, offset, None, size)

  def write(self, offset, data):
    self.access(1, offset, data, len(data))

  def read(self, offset, size):
    out = C.create_string_buffer(size)
    self.access(0, offset, out, size)
    return out.raw

  def mark_trial(self, manifest):
    if not self.lib.vgw_test_commit(self.slot, manifest.size, manifest.version):
      raise OSError('inner verification or trial commit failed')


def setup(library, update_library, key, slot=1, recovery=False, corrupt=False):
  images = [None, None]
  if not recovery:
    images[1-slot] = boot.image(key, 1-slot, 0, confirmed=True)
  flash = boot.Flash(library, key, *images)
  assert library.vgw_test_boot() == (-1 if recovery else 1-slot)
  image = boot_image.sign(key, payload(slot), boot.TARGET[:12], boot.TARGET[12:], slot, 3)
  if corrupt:
    image = image[:540] + bytes([image[540] ^ 1]) + image[541:]
  manifest = ImageManifest(boot.TARGET[:12], 1, boot.TARGET[12:], len(image), hashlib.sha256(image).digest(), b'b'*32, 3)
  bundle = key, image, sign_image(key, manifest)
  engine, env, _ = update.make(bundle, update_library, Storage(library, slot))
  return flash, engine, env, image


@pytest.mark.parametrize('slot', [0, 1])
@pytest.mark.parametrize('recovery', [False, True])
def test_authenticated_update_into_real_bootloader(library, update_library, key, slot, recovery):
  flash, engine, env, image = setup(library, update_library, key, slot, recovery)
  engine.begin()
  for offset in range(0, len(image), 256):
    env.now += 10
    chunk = image[offset:offset+256]
    assert engine.chunk(offset, chunk) == offset + len(chunk)
    before = flash.snapshot()
    assert engine.chunk(offset, chunk) == offset + len(chunk)
    assert flash.snapshot() == before
  engine.finish()
  assert engine.state == 'trial'
  assert library.vgw_test_boot() == slot
  assert library.vgw_test_confirm()
  assert library.vgw_test_boot() == slot


def test_valid_outer_signature_cannot_authorize_bad_inner_image(library, update_library, key):
  flash, engine, _, image = setup(library, update_library, key, corrupt=True)
  engine.begin()
  for offset in range(0, len(image), 256):
    engine.chunk(offset, image[offset:offset+256])
  with pytest.raises(AuthorityError):
    engine.finish()
  assert engine.state == 'aborted'
  assert flash.snapshot()[-16:] == b'\xff'*16
  assert library.vgw_test_boot() == 0


@pytest.mark.parametrize('mutation', range(1, 11))
@pytest.mark.parametrize('kind', [1, 2, 3, 4, 5])
def test_every_update_mutation_fault(library, update_library, key, mutation, kind):
  flash, engine, _, image = setup(library, update_library, key)
  # Five sector erases, four chunks, then trial marker. False-success erase
  # on already erased storage is harmless; write/readback faults must fail shut.
  assert 768 < len(image) <= 1024
  before = flash.snapshot()
  flash.fault(mutation, kind)
  try:
    engine.begin()
    for offset in range(0, len(image), 256):
      engine.chunk(offset, image[offset:offset+256])
    engine.finish()
  except AuthorityError:
    assert engine.state == 'aborted'
  assert flash.snapshot()[:boot.SLOTS[1]] == before[:boot.SLOTS[1]]
  flash.fault()
  assert library.vgw_test_boot() in (0, 1)
  # Trial not confirmed: always return to old confirmed slot on next boot.
  assert library.vgw_test_boot() == 0
