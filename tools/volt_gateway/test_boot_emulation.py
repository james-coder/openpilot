import os
from pathlib import Path

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway import boot_emulation, mcuboot_port, target_crypto
from openpilot.tools.volt_gateway.test_mcuboot_port import image, TARGET, SLOTS, SLOT_SIZE


@pytest.fixture(scope='module')
def elf(tmp_path_factory):
  checkout = os.environ.get('VOLTGW_MCUBOOT_CHECKOUT')
  if not checkout or not target_crypto.archive_path().is_file():
    pytest.skip('pinned boot/crypto dependencies absent; ARM boot gate NOT passed')
  return mcuboot_port.build(Path(checkout), target_crypto.archive_path(), tmp_path_factory.mktemp('boot-arm') / 'build', arm=True)


@pytest.fixture(scope='module')
def key():
  return ECC.generate(curve='P-256')


def execute(elf, key, flash, confirm=False):
  result = boot_emulation.run(elf, flash, key.public_key().export_key(format='DER'), TARGET, confirm=confirm)
  assert result['host_crypto_hooks'] == 0
  assert result['hardware_validated'] is False
  assert result['flash'][:SLOTS[0]] == flash[:SLOTS[0]]
  return result


@pytest.mark.parametrize('slot', [0, 1])
def test_actual_thumb_probe_and_confirmation(elf, key, slot):
  flash = b'\xff' * SLOTS[0] + b''.join(image(key, i, 2 if i == slot else 1, confirmed=i != slot, probe=True) for i in range(2))
  trial = execute(elf, key, flash, confirm=True)
  assert trial['selected'] == slot
  assert trial['probe'] == 0xa0 + slot
  assert trial['confirmed'] == 1
  assert trial['status'] == [1, slot, 0]
  assert trial['led_rgb'] == 4
  reboot = execute(elf, key, trial['flash'])
  assert reboot['selected'] == slot
  assert reboot['probe'] == 0xa0 + slot


def test_arm_trial_reverts_without_confirmation(elf, key):
  a = image(key, 0, 1, confirmed=True, probe=True)
  flash = b'\xff' * SLOTS[0] + a + image(key, 1, 2, probe=True)
  trial = execute(elf, key, flash)
  assert trial['selected'] == 1 and trial['probe'] == 0xa1
  assert trial['status'] == [2, 1, 0]
  assert trial['led_rgb'] == 2
  reboot = execute(elf, key, trial['flash'])
  assert reboot['selected'] == 0 and reboot['probe'] == 0xa0
  assert reboot['flash'][SLOTS[0]:SLOTS[1]] == a
  assert reboot['flash'][SLOTS[1]:] == b'\xff' * SLOT_SIZE


@pytest.mark.parametrize('defect', ['signature', 'target', 'vectors', 'both_invalid'])
def test_arm_never_executes_invalid_probe(elf, key, defect):
  a = image(key, 0, 1, confirmed=True, probe=True)
  b = bytearray(image(key, 1, 2, probe=True,
                      target=b'X'*44 if defect == 'target' else TARGET,
                      reset=0x08000001 if defect == 'vectors' else None))
  if defect == 'signature':
    b[540] ^= 1
  if defect == 'both_invalid':
    a, b = b'\xff' * SLOT_SIZE, b'\xff' * SLOT_SIZE
  result = execute(elf, key, b'\xff' * SLOTS[0] + a + bytes(b))
  if defect == 'signature':
    assert result['selected'] == 0 and result['probe'] == 0xa0
  else:
    assert result['selected'] < 0 and result['probe'] == 0
    code=1 if defect=='both_invalid' else 2
    assert result['status']==[5,255,code]
    # The harness samples at 4000ms. With human-countable fault groups,
    # code 2 is now in its intentional dark separator, not a lost fault.
    assert result['led_rgb']==(1 if code==1 else 0)


def test_led_absent_does_not_change_boot(elf, key):
  flash = b'\xff' * SLOTS[0] + image(key, 0, 1, confirmed=True, probe=True) + image(key, 1, 2, probe=True)
  on = execute(elf, key, flash, confirm=True)
  off = boot_emulation.run(elf, flash, key.public_key().export_key(format='DER'), TARGET, confirm=True, led=False)
  for field in ('selected', 'confirmed', 'probe', 'status', 'flash'):
    assert on[field] == off[field]
  assert off['led_rgb'] == off['led_bsrr'] == 0


@pytest.mark.parametrize('slot',[0,1])
def test_verified_loader_uses_real_msp_vtor_handoff(elf,key,slot):
  flash = b'\xff'*SLOTS[0] + b''.join(image(key,i,2 if i==slot else 1,confirmed=i!=slot) for i in range(2))
  result = boot_emulation.run(elf,flash,key.public_key().export_key(format='DER'),TARGET,physical_handoff=True)
  start = 0x08000000+SLOTS[slot]+512
  assert result['handoff']=={'pc':start+8,'sp':0x20020000,'primask':1,'vtor':start}
  assert result['selected']==slot and result['confirmed']==0
  # Stopping at reset entry is NOT proof of successful app startup. Reboot
  # without confirmation must take the old slot, using the same real handoff.
  reboot = boot_emulation.run(elf,result['flash'],key.public_key().export_key(format='DER'),TARGET,physical_handoff=True)
  assert reboot['selected']==1-slot


def test_both_invalid_never_handoff(elf,key):
  result = boot_emulation.run(elf,b'\xff'*0x100000,key.public_key().export_key(format='DER'),TARGET,physical_handoff=True)
  assert result['selected']==-1 and result['handoff'] is None
