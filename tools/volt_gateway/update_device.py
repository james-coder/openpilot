"""Development workstation only: paired, independently signed firmware update.

Private signing material never crosses USB/CAN. The comma may relay the public
wire messages, but must not run this interactive operator tool or store its key.
No bootloader, provisioning, arbitrary memory-write or automatic-reset command.
"""
import argparse
import getpass
import hashlib
import io
import json
from pathlib import Path
import resource
import sys
import tarfile

from openpilot.tools.volt_gateway import boot_image
from openpilot.tools.volt_gateway.authority import AuthorityError, Challenge, SignedImage, public_key
from openpilot.tools.volt_gateway.device_cli import Device, credentials
from openpilot.tools.volt_gateway.operator import PRIVATE_ROOT, authorize_image, bounded_read, load_key
from openpilot.tools.volt_gateway.usb_transport import UsbTransport


def unpack_release(raw: bytes, trusted_public: bytes):
  if not isinstance(raw,bytes) or len(raw)>boot_image.SLOT_SIZE+32768:
    raise AuthorityError('release archive too large')
  expected={'image.bin','manifest.bin','firmware-public.der','checksums.json'}
  contents={}
  with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
    for entry in archive:
      limit=boot_image.SLOT_SIZE-64 if entry.name=='image.bin' else 2048
      if (entry.name not in expected or entry.name in contents or not entry.isfile() or
          not 0<entry.size<=limit or entry.pax_headers):
        raise AuthorityError('release member/type/size rejected')
      with archive.extractfile(entry) as stream:
        contents[entry.name]=stream.read(limit+1)
      if len(contents[entry.name])!=entry.size:
        raise AuthorityError('truncated release member')
  if set(contents)!=expected:
    raise AuthorityError('release member set')
  trusted=public_key(trusted_public)
  if contents['firmware-public.der']!=trusted.export_key(format='DER'):
    raise AuthorityError('release key is not provisioned verification key')
  manifest=SignedImage.unpack(contents['manifest.bin'])
  manifest.verify(trusted)
  image=contents['image.bin']
  if len(image)!=manifest.manifest.size or hashlib.sha256(image).digest()!=manifest.manifest.digest:
    raise AuthorityError('release image hash/size')
  checksums=json.loads(contents['checksums.json'])
  if checksums!={name:hashlib.sha256(contents[name]).hexdigest() for name in expected-{'checksums.json'}}:
    raise AuthorityError('release checksums')
  return image,manifest


def transfer(device,image,manifest,verification_key,authorize,*,progress=lambda offset,total:None):
  """Transport-independent transfer. Device.command retries exact MAC/sequence.

  authorize receives a fresh gateway challenge; it returns an independently
  signed PROGRAM grant, not the routine pairing key. No vehicle state override.
  """
  target=device.command(0x88)
  if len(target)!=5 or int.from_bytes(target[:4],'big')!=boot_image.SLOT_SIZE or target[4]>1:
    raise AuthorityError('unexpected gateway update target/layout')
  m=manifest.manifest
  boot_image.verify(image,verification_key,m.device,m.layout,target[4],m.version)
  challenge=Challenge.unpack(device.command(0x80,manifest.pack()))
  grant=authorize(challenge)
  device.command(0x81,grant)
  device.command(0x82)  # device continuously enforces Park/awake/stable VIN
  for offset in range(0,len(image),256):
    chunk=image[offset:offset+256]
    device.command(0x83,offset.to_bytes(4,'big')+chunk)
    progress(offset+len(chunk),len(image))
  device.command(0x84)
  if device.command(0x85)!=b'\2'+len(image).to_bytes(4,'big'):
    raise AuthorityError('gateway did not report committed trial')
  return target[4]


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pairing',type=Path,required=True)
  parser.add_argument('--release',type=Path,required=True)
  args=parser.parse_args()
  if not sys.stdin.isatty():
    parser.error('local interactive terminal required; never run on comma')
  keys=credentials(args.pairing)
  directory=PRIVATE_ROOT/keys['device'].hex()
  public=bounded_read(directory/'firmware-public.der',1024)
  image,manifest=unpack_release(bounded_read(args.release,boot_image.SLOT_SIZE+32768),public)
  if manifest.manifest.device!=keys['device'] or manifest.manifest.layout!=keys['layout']:
    parser.error('release is for a different gateway/layout')
  print(json.dumps({'device':keys['device'].hex(),'sha256':manifest.manifest.digest.hex(),
                    'size':len(image),'version':manifest.manifest.version}))
  # Unlock before starting an expiring vehicle session, so human typing time
  # cannot exhaust a PROGRAM challenge or require background signing services.
  key=load_key(directory/'firmware-key.pem',getpass.getpass('Unlock local key to authorize this parked update: '))
  if key.public_key().export_key(format='DER')!=public:
    raise AuthorityError('local signer does not match provisioned public key')
  with UsbTransport(keys['device'].hex()) as transport:
    device=Device(transport,keys)
    try:
      slot=transfer(device,image,manifest,public,
        lambda challenge:authorize_image(key,manifest,challenge,1800000).pack(),
        progress=lambda offset,total:print(f'Verified chunk {offset}/{total}',flush=True))
      print(f'Signed trial committed to slot {"AB"[slot]}. No reboot requested; previous confirmed slot retained.')
    finally:
      device.close()


if __name__=='__main__':
  main()
