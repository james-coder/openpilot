import hashlib
import io
import struct
import tarfile

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway import boot_image
from openpilot.tools.volt_gateway.authority import AuthorityError, ImageManifest
from openpilot.tools.volt_gateway.operator import sign_image
from openpilot.tools.volt_gateway import test_mcuboot_port as boot_tests
from openpilot.tools.volt_gateway.test_mcuboot_port import Flash, TARGET, SLOT_SIZE

library = boot_tests.library


@pytest.fixture(scope='module')
def key():
  return ECC.generate(curve='P-256')


def payload(slot):
  start = 0x08000000 + boot_image.SLOTS[slot] + 512
  return struct.pack('<II', 0x20020000, start + 9) + b'\x70\x47' + bytes(54)


def trial(image):
  return image.ljust(SLOT_SIZE - 16, b'\xff') + boot_image.MAGIC


@pytest.mark.parametrize('slot', [0, 1])
def test_signed_release_boots_real_loader(library, key, slot):
  package = boot_image.release(key, payload(slot), TARGET[:12], TARGET[12:], slot, 0xffffffff, b'b'*32)
  with tarfile.open(fileobj=io.BytesIO(package)) as archive:
    image = archive.extractfile('image.bin').read()
    assert len(image) < SLOT_SIZE - 64  # commit trailer never shipped
  public = key.public_key().export_key(format='DER')
  boot_image.verify(image, public, TARGET[:12], TARGET[12:], slot, 0xffffffff)
  slots = [None, None]
  slots[slot] = trial(image)
  flash = Flash(library, key, *slots)
  assert library.vgw_test_boot() == slot
  assert library.vgw_test_confirm() == 1
  assert library.vgw_test_boot() == slot
  flash.snapshot()


@pytest.mark.parametrize('offset', [0, 4, 8, 10, 12, 16, 20, 24, 28, 40, 512, 540, 580, 628, 650, 700])
def test_corruption_rejected(key, offset):
  image = bytearray(boot_image.sign(key, payload(0), TARGET[:12], TARGET[12:], 0, 1))
  image[offset] ^= 1
  with pytest.raises(AuthorityError):
    boot_image.verify(bytes(image), key.public_key().export_key(format='DER'), TARGET[:12], TARGET[12:], 0, 1)


@pytest.mark.parametrize('defect', ['slot', 'target', 'layout', 'version', 'key', 'trailing', 'truncated'])
def test_release_binding(key, defect):
  image = boot_image.sign(key, payload(0), TARGET[:12], TARGET[12:], 0, 1)
  public = key.public_key().export_key(format='DER')
  args = [TARGET[:12], TARGET[12:], 0, 1]
  if defect == 'slot':
    args[2] = 1
  elif defect == 'target':
    args[0] = b'x'*12
  elif defect == 'layout':
    args[1] = b'x'*32
  elif defect == 'version':
    args[3] = 2
  elif defect == 'key':
    public = ECC.generate(curve='P-256').public_key().export_key(format='DER')
  elif defect == 'trailing':
    image += b'\xff'*4
  else:
    image = image[:-4]
  with pytest.raises(AuthorityError):
    boot_image.verify(image, public, *args)


def test_outer_signature_cannot_substitute_for_inner(key):
  image = bytearray(boot_image.sign(key, payload(0), TARGET[:12], TARGET[12:], 0, 1))
  image[540] ^= 1
  image = bytes(image)
  m = ImageManifest(TARGET[:12], 1, TARGET[12:], len(image), hashlib.sha256(image).digest(), b'b'*32, 1)
  signed = sign_image(key, m).pack()
  with pytest.raises(AuthorityError):
    boot_image.verified_package(image, signed, key.public_key().export_key(format='DER'), 0)


def test_wrong_link_address_rejected_before_signing(key):
  with pytest.raises(AuthorityError):
    boot_image.sign(key, payload(0), TARGET[:12], TARGET[12:], 1, 1)
