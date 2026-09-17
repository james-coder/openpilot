import struct
import zlib

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway import bench_image, boot_image, provisioning
from openpilot.tools.volt_gateway.authority import AuthorityError
from openpilot.tools.volt_gateway.release import reject_secret


def fixtures():
  key=ECC.generate(curve='P-256')
  record,_=provisioning.passive_record(b'test-device!',b'p'*32,key.public_key().export_key(format='DER'),swcan=3,divider=8862)
  loader=struct.pack('<II',0x20020000,0x08000009)+bytes(504)
  apps=tuple(struct.pack('<II',0x20020000,0x08000000+offset+521)+b'\0\xbf'*252 for offset in boot_image.SLOTS)
  return key,record,loader,apps


def test_private_initial_image_exact_geometry_confirmed_signed_slots():
  key,record,loader,apps=fixtures()
  image=bench_image.assemble(loader,apps,record,key)
  assert len(image)==0x100000
  assert image[:len(loader)]==loader and image[0x20000:0x20100]==record
  assert image[len(loader):0x20000]==b'\xff'*(0x20000-len(loader))
  assert image[0x20100:0x40000]==b'\xff'*(0x40000-0x20100)
  for slot,offset in enumerate(boot_image.SLOTS):
    end=offset+boot_image.SLOT_SIZE
    assert image[end-32]==image[end-24]==1
    assert image[end-16:end]==boot_image.MAGIC
    unsigned_end=512+len(apps[slot])+52
    total=struct.unpack_from('<H',image,offset+unsigned_end+2)[0]
    size=(unsigned_end+total+3)&~3
    boot_image.verify(image[offset:offset+size],key.public_key().export_key(format='DER'),record[8:20],record[20:52],slot,2-slot)
  with pytest.raises(AuthorityError):
    reject_secret('factory-flash.bin',image)


@pytest.mark.parametrize('offset',[0,20,52,84,148,239,240,241,242,243,244,246,248,252])
def test_corrupt_or_noncanonical_provisioning_never_packaged(offset):
  key,record,loader,apps=fixtures()
  changed=bytearray(record)
  changed[offset]^=1
  if offset!=252:
    changed[252:]=struct.pack('>I',zlib.crc32(changed[:252]))
  with pytest.raises((ValueError,AuthorityError)):
    bench_image.assemble(loader,apps,bytes(changed),key)


def test_wrong_signer_slot_or_loader_rejected():
  key,record,loader,apps=fixtures()
  with pytest.raises(ValueError):
    bench_image.assemble(loader,apps,record,ECC.generate(curve='P-256'))
  with pytest.raises(AuthorityError):
    bench_image.assemble(loader,apps[::-1],record,key)
  with pytest.raises(ValueError):
    bench_image.assemble(b'\0'*512,apps,record,key)


def test_object_profile_requires_explicit_selection_and_exact_ids():
  key, old, loader, apps = fixtures()
  record, _ = provisioning.object_trial_record(old[8:20], old[116:148], key.public_key().export_key(format='DER'), divider=8862)
  with pytest.raises(ValueError):
    bench_image.assemble(loader, apps, record, key)
  image = bench_image.assemble(loader, apps, record, key, object_parked_trial=True)
  assert image[0x20000:0x20100] == record
  for offset in (239, 240, 241, 242, 243, 244, 245, 246, 247):
    changed = bytearray(record)
    changed[offset] ^= 1
    changed[252:] = struct.pack('>I', zlib.crc32(changed[:252]))
    with pytest.raises(ValueError):
      bench_image.assemble(loader, apps, bytes(changed), key, object_parked_trial=True)
