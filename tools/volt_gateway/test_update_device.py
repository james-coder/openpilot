import io
import tarfile

from Crypto.PublicKey import ECC
import pytest

from openpilot.tools.volt_gateway import test_recovery_boot as boot
from openpilot.tools.volt_gateway.authority import AuthorityError
from openpilot.tools.volt_gateway.operator import authorize_image
from openpilot.tools.volt_gateway.release import package
from openpilot.tools.volt_gateway.test_device_cli import device
from openpilot.tools.volt_gateway.update_device import transfer, unpack_release

library=boot.library
key=boot.key
archive=boot.archive
native=boot.native
service_library=boot.service_library


@pytest.mark.parametrize('slot',[0,1])
@pytest.mark.parametrize('lose_reply',[False,True])
def test_host_transfer_usb_real_c_signed_boot_revert(library,service_library,native,key,slot,lose_reply,monkeypatch):
  monkeypatch.setattr('openpilot.tools.volt_gateway.usb_transport.time.sleep',lambda _:None)
  flash,r=boot.setup(library,service_library,native,key,slot,authorize=False)
  d=device(r)
  public=key.public_key().export_key(format='DER')
  image,manifest=unpack_release(package(r.image,r.manifest.pack(),public),public)
  exchange=d.transport.exchange
  losses=[]
  def lossy(packet,**kwargs):
    response=exchange(packet,**kwargs)
    if lose_reply and packet[7]==0x83 and not losses:
      losses.append(library.vgw_test_mutations())
      raise TimeoutError('modeled lost reply after physical write')
    if losses and packet[7]==0x83 and len(losses)==1:
      assert library.vgw_test_mutations()==losses[0]  # exact retry never writes twice
      losses.append(True)
    return response
  d.transport.exchange=lossy
  before=flash.snapshot()
  assert transfer(d,image,manifest,public,lambda ch:authorize_image(key,manifest,ch,60000).pack())==slot
  assert library.vgw_test_boot()==slot
  assert library.vgw_test_boot()==1-slot  # unconfirmed trial reverts
  start=boot.boot.SLOTS[1-slot]
  assert flash.snapshot()[start:start+boot.boot.SLOT_SIZE]==before[start:start+boot.boot.SLOT_SIZE]


def test_package_rejects_untrusted_key_and_nonfiles(key):
  raw=io.BytesIO()
  with tarfile.open(fileobj=raw,mode='w') as archive:
    entry=tarfile.TarInfo('image.bin')
    entry.type=tarfile.SYMTYPE
    entry.linkname='/not-a-file'
    archive.addfile(entry)
  with pytest.raises(AuthorityError):
    unpack_release(raw.getvalue(),key.public_key().export_key(format='DER'))
  from openpilot.tools.volt_gateway.test_boot_image import payload
  from openpilot.tools.volt_gateway.boot_image import release
  signed=release(key,payload(0),b'device-test!',b'l'*32,0,1,b'b'*32)
  other=ECC.generate(curve='P-256').public_key().export_key(format='DER')
  with pytest.raises(AuthorityError,match='not provisioned'):
    unpack_release(signed,other)
