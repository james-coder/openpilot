from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway.object_trial_prepare import predecessor
from openpilot.tools.volt_gateway.provisioning import passive_record, object_trial_record


@pytest.mark.parametrize('trial', [False, True])
def test_only_exact_predecessor_is_accepted(trial):
  device = b'test-device!'
  public = ECC.generate(curve='P-256').public_key().export_key(format='DER')
  factory = object_trial_record if trial else passive_record
  record, _ = factory(device, b'p'*32, public, divider=8862, **({} if trial else {'swcan': 3}))
  flash = bytearray(b'\xff'*1048576)
  flash[0x20000:0x20100] = record
  assert predecessor(flash, device.hex()) == record
  with pytest.raises(ValueError):
    predecessor(flash, (b'x'*12).hex())
  with pytest.raises(ValueError):
    predecessor(flash[:-1], device.hex())
  for offset in (0, 52, 244, 252):
    changed = bytearray(flash)
    changed[0x20000+offset] ^= 1
    with pytest.raises(ValueError):
      predecessor(changed, device.hex())
