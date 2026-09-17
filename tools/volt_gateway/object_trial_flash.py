"""Exact labeled White Panda bench migration. Requires OBD disconnected.

ROM DFU only; no reset, option-byte change, erase-all or other-device matching.
Full live readback must equal the preserved predecessor before programming.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess

from openpilot.tools.volt_gateway.operator import bounded_read, private_directory, write_new
from openpilot.tools.volt_gateway.provisioning import object_trial_record

SERIAL = '370022000651363038363036'
REGIONS = [(0, 0x10000), (0x20000, 0x20000), (0x40000, 0x20000), (0xa0000, 0x20000)]


def validate(before, image, manifest):
  if (len(before) != 1048576 or len(image) != 1048576 or manifest['device'] != SERIAL or
      hashlib.sha256(before).hexdigest() != manifest['before_sha256'] or
      hashlib.sha256(image).hexdigest() != manifest['after_sha256']):
    raise ValueError('exact image identity/readback mismatch')
  old, new = before[0x20000:0x20100], image[0x20000:0x20100]
  if old[8:20].hex() != SERIAL:
    raise ValueError('wrong predecessor identity')
  expected, _ = object_trial_record(old[8:20], old[116:148], old[148:239], divider=int.from_bytes(old[248:252], 'big'))
  if new != expected or before[0x20100:0x40000] != image[0x20100:0x40000]:
    raise ValueError('provisioning migration not canonical or changed unused provisioning area')
  reconstructed = bytearray(before)
  for offset, size in REGIONS:
    reconstructed[offset:offset+size] = image[offset:offset+size]
  if reconstructed != image:
    raise ValueError('unexpected changes outside reviewed regions')


def main():
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--prepared', type=Path, required=True)
  parser.add_argument('--before', type=Path, required=True)
  parser.add_argument('--evidence', type=Path, required=True)
  parser.add_argument('--obd-disconnected', action='store_true', required=True)
  args = parser.parse_args()
  before = bounded_read(args.before, 1048576, private=True)
  image = bounded_read(args.prepared/'factory-flash.bin', 1048576, private=True)
  manifest = json.loads(bounded_read(args.prepared/'manifest.json', 4096, private=True))
  validate(before, image, manifest)
  private_directory(args.evidence)
  base = ['dfu-util', '-d', '0483:df11', '-S', '365236793036', '-a', '0']
  def run(name, options):
    with (args.evidence/(name+'.log')).open('x') as log:
      subprocess.run(base+options, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
  pre = args.evidence/'before.bin'
  run('read-before', ['-s', '0x08000000:1048576', '-U', str(pre)])
  pre.chmod(0o600)
  if pre.read_bytes() != before:
    raise ValueError('LIVE READBACK MISMATCH: no programming attempted')
  for offset, size in REGIONS:
    part = args.evidence/f'sector-{offset:06x}.bin'
    write_new(part, image[offset:offset+size])
    run(f'program-{offset:06x}', ['-s', hex(0x08000000+offset), '-D', str(part)])
  after = args.evidence/'after.bin'
  run('read-after', ['-s', '0x08000000:1048576', '-U', str(after)])
  after.chmod(0o600)
  if after.read_bytes() != image:
    raise ValueError('READBACK MISMATCH: DO NOT BOOT')
  print('Full 1 MiB readback verified:', hashlib.sha256(image).hexdigest())


if __name__ == '__main__':
  main()
