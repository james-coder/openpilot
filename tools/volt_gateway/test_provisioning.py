import struct
import zlib

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.provisioning import passive_record, LAYOUT
from openpilot.tools.volt_gateway.provisioning import object_trial_record
from openpilot.tools.volt_gateway.release import reject_secret
from openpilot.tools.volt_gateway.authority import AuthorityError


def test_object_trial_exact_mapping_and_policy_separation():
  public = ECC.generate(curve='P-256').public_key().export_key(format='DER')
  old, old_keys = passive_record(b'device-test!', b'p'*32, public, swcan=3, divider=8862)
  new, keys = object_trial_record(b'device-test!', b'p'*32, public, divider=8862)
  assert new[:52] == old[:52] and new[84:239] == old[84:239]
  assert keys['pairing'] == old_keys['pairing'] and keys['policy'] != old_keys['policy']
  assert new[239:248] == bytes.fromhex('03 03 02 00 00 06 f0 06 f1')
  assert struct.unpack('>I', new[252:])[0] == zlib.crc32(new[:252])


@pytest.mark.parametrize('swcan',[2,3])
@pytest.mark.parametrize('divider',[3791,8862])
def test_passive_record_exact_layout_no_can_tx(swcan,divider):
  public=ECC.generate(curve='P-256').public_key().export_key(format='DER')
  record,bundle=passive_record(b'device-test!',b'p'*32,public,swcan=swcan,divider=divider)
  assert len(record)==256 and record[:8]==b'VGWCFG1\0'
  assert record[20:52]==LAYOUT and record[148:239]==public
  assert record[239]==swcan and not record[240]&(1<<(swcan-1))
  assert record[241:248]==bytes(7)  # no backhaul; no usable CAN transport IDs
  assert int.from_bytes(record[248:252],'big')==divider
  assert struct.unpack('>I',record[252:])[0]==zlib.crc32(record[:252])
  assert bytes.fromhex(bundle['pairing'])==record[116:148]
  for name in ('pairing.json','provisioning.bin'):
    with pytest.raises(AuthorityError):
      reject_secret(name,record)


@pytest.mark.parametrize('device,pairing,swcan,divider',[
  (bytes(12),b'p'*32,3,8862),(b'device-test!',bytes(32),3,8862),
  (b'device-test!',b'p'*32,1,8862),(b'device-test!',b'p'*32,3,1)])
def test_invalid_provisioning(device,pairing,swcan,divider):
  public=ECC.generate(curve='P-256').public_key().export_key(format='DER')
  with pytest.raises(ValueError):
    passive_record(device,pairing,public,swcan=swcan,divider=divider)
