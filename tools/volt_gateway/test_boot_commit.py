"""Real MCUboot candidate validation/commit, with simulated flash and power cuts."""
import pytest

from openpilot.tools.volt_gateway import boot_image
from openpilot.tools.volt_gateway import test_mcuboot_port as boot
from openpilot.tools.volt_gateway.test_boot_image import payload

library = boot.library
key = boot.key


def setup(lib, key, slot=1, recovery=False):
  images = [None, None]
  if not recovery:
    images[1-slot] = boot.image(key, 1-slot, 0, confirmed=True)
  flash = boot.Flash(lib, key, *images)
  assert lib.vgw_test_boot() == (-1 if recovery else 1-slot)
  image = boot_image.sign(key, payload(slot), boot.TARGET[:12], boot.TARGET[12:], slot, 2)
  assert lib.vgw_test_stage(slot, image, len(image)) == 0
  return flash, image


@pytest.mark.parametrize('slot', [0, 1])
@pytest.mark.parametrize('recovery', [False, True])
@pytest.mark.parametrize('confirm', [False, True])
def test_commit_trial_confirm_or_revert(library, key, slot, recovery, confirm):
  flash, image = setup(library, key, slot, recovery)
  before = flash.snapshot()
  assert library.vgw_boot_validate_candidate(slot, len(image), 2)
  assert flash.snapshot() == before  # validation is read-only
  assert library.vgw_test_commit(slot, len(image), 2)
  after = flash.snapshot()
  end = boot.SLOTS[slot] + boot.SLOT_SIZE
  assert after[:end-16] == before[:end-16]
  assert after[end:] == before[end:]
  assert after[end-16:end] == boot.MAGIC
  assert not library.vgw_test_commit(slot, len(image), 2)  # not an idempotent write primitive
  assert library.vgw_test_boot() == slot
  if confirm:
    assert library.vgw_test_confirm()
  assert library.vgw_test_boot() == (slot if confirm else -1 if recovery else 1-slot)


@pytest.mark.parametrize('defect', ['payload', 'signature', 'version', 'slot', 'truncated', 'extra'])
def test_invalid_candidate_never_writes(library, key, defect):
  flash, image = setup(library, key)
  size, version, slot = len(image), 2, 1
  if defect in ('payload', 'signature'):
    damaged = bytearray(image)
    damaged[540 if defect == 'payload' else -8] ^= 1
    assert library.vgw_test_stage(slot, bytes(damaged), size) == 0
  elif defect == 'version':
    version = 3
  elif defect == 'slot':
    slot = 0
  elif defect == 'truncated':
    size -= 4
  else:
    size += 4
  before = flash.snapshot()
  assert not library.vgw_test_commit(slot, size, version)
  assert flash.snapshot() == before


@pytest.mark.parametrize('kind', [1, 2, 3, 4, 5])
def test_commit_power_cut_or_storage_fault_preserves_fallback(library, key, kind):
  flash, image = setup(library, key)
  before = flash.snapshot()
  flash.fault(1, kind)
  assert not library.vgw_test_commit(1, len(image), 2)
  assert flash.snapshot()[:boot.SLOTS[1]] == before[:boot.SLOTS[1]]
  flash.fault()
  chosen = library.vgw_test_boot()
  # Power loss after the complete marker write may boot a verified trial even
  # though the host never got an acknowledgement. Neither case bricks recovery.
  assert chosen in (0, 1)
  assert library.vgw_test_boot() == 0


@pytest.mark.parametrize('cut', [0, 1, 256, 512, 700])
def test_incomplete_image_without_commit_keeps_fallback(library, key, cut):
  flash, image = setup(library, key)
  partial = image[:cut] or b'\xff'
  assert library.vgw_test_stage(1, partial, len(partial)) == 0
  assert library.vgw_test_boot() == 0
  flash.snapshot()
