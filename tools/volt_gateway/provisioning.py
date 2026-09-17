"""Create private, bench-only provisioning artifacts. Never touches hardware.

The first supported profile is USB-commanded and CAN listen-only. Shared-bus
TX cannot be enabled by this tool until its separate ID/wiring gates are met.
Only the public firmware verification key is read. No signing key is generated.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import secrets
import struct
import zlib

from openpilot.tools.volt_gateway.authority import public_key
from openpilot.tools.volt_gateway.operator import bounded_read, private_directory, write_new
from openpilot.tools.volt_gateway.protocol import OBJECT_REQUEST_ID, OBJECT_RESPONSE_ID

LAYOUT = hashlib.sha256(b'VOLTGW-F413-v1:flash=1024K;ram=128K;loader=0:128K;provision=128K:128K;'+
                        b'A=256K:384K;B=640K:384K;header=512;trailer=64;direct-xip-revert').digest()

def object_trial_record(device: bytes, pairing: bytes, public: bytes, *, divider: int):
  """Explicit parked-trial profile, never the CLI default; no vehicle actuation.

  Physical CAN2 is Object, CAN3 is SWCAN. Preserve identity/secret/key across
  the bench migration but bind sessions to a different policy hash.
  """
  record, credentials = passive_record(device, pairing, public, swcan=3, divider=divider)
  record = bytearray(record)
  mapping = bytes([3, 3, 2, 0, 0]) + struct.pack('>HHI', OBJECT_REQUEST_ID, OBJECT_RESPONSE_ID, divider)
  policy = hashlib.sha256(b'VOLTGW-POLICY-v1:Object-parked-trial;no-vehicle-TX;'+mapping).digest()
  record[52:84] = policy
  record[239:252] = mapping
  record[252:] = struct.pack('>I', zlib.crc32(record[:252]))
  credentials['policy'] = policy.hex()
  return bytes(record), credentials


def passive_record(device: bytes, pairing: bytes, public: bytes, *, swcan: int, divider: int):
  if len(device)!=12 or not any(device) or len(pairing)!=32 or not any(pairing):
    raise ValueError('nonzero device identity and pairing secret required')
  if swcan not in (2,3) or divider not in (3791,8862):
    raise ValueError('verified White SWCAN controller and voltage divider required')
  key=public_key(public)
  der=key.export_key(format='DER')
  if len(der)!=91:
    raise ValueError('canonical P-256 public key required')
  # Primary HSCAN remains CAN1 in either mux choice. Both other physically
  # available HSCAN controllers are listeners; no private transport IDs exist.
  mapping=bytes([swcan,7&~(1<<(swcan-1)),0,0,0])+struct.pack('>HHI',0,0,divider)
  policy=hashlib.sha256(b'VOLTGW-POLICY-v1:USB-only;CAN-silent;no-vehicle-TX;'+mapping).digest()
  record=b'VGWCFG1\0'+device+LAYOUT+policy+bytes(32)+pairing+der+mapping
  assert len(record)==252
  record+=struct.pack('>I',zlib.crc32(record))
  credentials={'device':device.hex(),'layout':LAYOUT.hex(),'policy':policy.hex(),'pairing':pairing.hex()}
  return record,credentials


def main():
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--device',required=True)
  parser.add_argument('--public-key',type=Path,required=True)
  parser.add_argument('--swcan-controller',type=int,choices=(2,3),required=True)
  parser.add_argument('--verified-divider',type=int,choices=(3791,8862),required=True)
  parser.add_argument('--output',type=Path,required=True,help='new private directory outside tracked release artifacts')
  args=parser.parse_args()
  if len(args.device)!=24 or args.output.exists():
    parser.error('24-hex device and new output directory required')
  record,credentials=passive_record(bytes.fromhex(args.device),secrets.token_bytes(32),
    bounded_read(args.public_key,1024),swcan=args.swcan_controller,divider=args.verified_divider)
  private_directory(args.output)
  write_new(args.output/'provisioning.bin',record)
  write_new(args.output/'pairing.json',(json.dumps(credentials,sort_keys=True)+'\n').encode())
  print('Created owner-only provisioning and pairing artifacts. No device changed. Back up encrypted off-device; never commit these files.')


if __name__=='__main__':
  main()
