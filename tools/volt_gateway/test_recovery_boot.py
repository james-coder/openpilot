"""Authenticated wire PDUs -> C recovery/updater -> actual MCUboot C selection.

All storage and safe-state samples are modeled; this is not physical recovery.
"""
import hashlib

import pytest

from openpilot.tools.volt_gateway import boot_image
from openpilot.tools.volt_gateway import test_mcuboot_port as boot
from openpilot.tools.volt_gateway import test_recovery_service as recovery
from openpilot.tools.volt_gateway.authority import ImageManifest
from openpilot.tools.volt_gateway.operator import sign_image
from openpilot.tools.volt_gateway.test_boot_image import payload
from openpilot.tools.volt_gateway.test_update_boot_integration import Storage

library=boot.library
key=boot.key
archive=recovery.archive
native=recovery.native
service_library=recovery.service_library


def setup(library,service_library,native,key,slot=1,empty=False,transport=None,authorize=True):
  images=[None,None]
  if not empty:
    images[1-slot]=boot.image(key,1-slot,0,confirmed=True)
  flash=boot.Flash(library,key,*images)
  assert library.vgw_test_boot()==(-1 if empty else 1-slot)
  image=boot_image.sign(key,payload(slot),boot.TARGET[:12],boot.TARGET[12:],slot,3)
  manifest=ImageManifest(boot.TARGET[:12],1,boot.TARGET[12:],len(image),hashlib.sha256(image).digest(),b'b'*32,3)
  r=recovery.Recovery(service_library,native,(key,image,sign_image(key,manifest)),Storage(library,slot),transport=transport)
  if authorize:
    r.authorize()
  return flash,r


@pytest.mark.parametrize('slot',[0,1])
@pytest.mark.parametrize('empty',[False,True])
@pytest.mark.parametrize('confirm',[False,True])
def test_wire_update_select_confirm_or_revert(library,service_library,native,key,slot,empty,confirm):
  flash,r=setup(library,service_library,native,key,slot,empty)
  assert r.command(0x82)==b'\0'
  for off in range(0,len(r.image),256):
    request=r.client.request(0x83,off.to_bytes(4,'big')+r.image[off:off+256])
    reply=r.wire(request)
    saved=flash.snapshot()
    assert r.wire(request)==reply and flash.snapshot()==saved
    assert r.client.accept(reply)==b'\0'
  request=r.client.request(0x84)
  reply=r.wire(request)
  saved=flash.snapshot()
  assert r.wire(request)==reply and flash.snapshot()==saved
  assert r.client.accept(reply)==b'\0'
  assert library.vgw_test_boot()==slot
  if confirm:
    assert library.vgw_test_confirm()
  assert library.vgw_test_boot()==(slot if confirm else -1 if empty else 1-slot)


@pytest.mark.parametrize('mutation',range(1,11))
@pytest.mark.parametrize('kind',[1,2,3,4,5])
def test_wire_update_every_mutation_failure(library,service_library,native,key,mutation,kind):
  flash,r=setup(library,service_library,native,key)
  before=flash.snapshot()
  flash.fault(mutation,kind)
  if r.command(0x82)==b'\0':
    for off in range(0,len(r.image),256):
      if r.command(0x83,off.to_bytes(4,'big')+r.image[off:off+256])!=b'\0':
        break
    else:
      r.command(0x84)
  assert flash.snapshot()[:boot.SLOTS[1]]==before[:boot.SLOTS[1]]
  flash.fault()
  assert library.vgw_test_boot() in (0,1)
  assert library.vgw_test_boot()==0
