"""Prepare private signed Object-trial image from verified bench build; no IO to hardware.

Preserves device identity, pairing secret and firmware key. The policy hash
changes. Only the resulting pairing.json (never the factory image/key) belongs
on the comma. Existing CAN-silent readback remains the rollback image.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import resource

from openpilot.tools.volt_gateway.bench_image import assemble, payload
from openpilot.tools.volt_gateway.operator import bounded_read, load_key, private_directory, write_new
from openpilot.tools.volt_gateway.provisioning import passive_record, object_trial_record


def main():
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--build', type=Path, required=True)
  parser.add_argument('--private-device', type=Path, required=True)
  parser.add_argument('--env', type=Path, required=True)
  parser.add_argument('--before', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  before = bounded_read(args.before, 1048576, private=True)
  if len(before) != 1048576:
    raise ValueError('full prior readback required')
  old = before[0x20000:0x20100]
  public = old[148:239]
  canonical, _ = passive_record(old[8:20], old[116:148], public, swcan=3, divider=int.from_bytes(old[248:252], 'big'))
  if old != canonical or old[8:20].hex() != args.private_device.name:
    raise ValueError('only exact identified CAN-silent predecessor accepted')
  report = json.loads(bounded_read(args.build/'report.json', 65536))
  parts = []
  for name, start, size in [('loader', 0x08000000, 0x20000), ('A', 0x08040200, 0x5fa00), ('B', 0x080a0200, 0x5fa00)]:
    raw = bounded_read(args.build/f'NOT_RELEASED-{name}.elf', 2*1024*1024)
    if hashlib.sha256(raw).hexdigest() != report['images'][name]['sha256']:
      raise ValueError('board build identity mismatch')
    parts.append(payload(raw, start, size))
  lines = bounded_read(args.env, 65536, private=True).decode().splitlines()
  matches = [line.split('=', 1)[1].strip() for line in lines if line.startswith('SIGNING_KEY_PASSPHRASE=')]
  if len(matches) != 1:
    raise ValueError('one local signing passphrase entry required')
  password = ast.literal_eval(matches[0]) if matches[0].startswith(('"', "'")) else matches[0]
  key = load_key(args.private_device/'firmware-key.pem', password)
  del password, matches, lines
  record, credentials = object_trial_record(old[8:20], old[116:148], public, divider=int.from_bytes(old[248:252], 'big'))
  image = assemble(parts[0], tuple(parts[1:]), record, key, object_parked_trial=True)
  del key
  regions = [(0, 0x10000), (0x20000, 0x20000), (0x40000, 0x20000), (0xa0000, 0x20000)]
  expected = bytearray(before)
  for offset, size in regions:
    expected[offset:offset+size] = image[offset:offset+size]
  if expected != image:
    raise ValueError('unexpected changes outside reviewed flash sectors')
  private_directory(args.output)
  write_new(args.output/'factory-flash.bin', image)
  write_new(args.output/'provisioning.bin', record)
  write_new(args.output/'pairing.json', (json.dumps(credentials)+'\n').encode())
  manifest = {'before_sha256': hashlib.sha256(before).hexdigest(), 'after_sha256': hashlib.sha256(image).hexdigest(),
              'device': old[8:20].hex(), 'regions': regions, 'contains_secrets': True, 'flashed': False}
  write_new(args.output/'manifest.json', (json.dumps(manifest, indent=2)+'\n').encode())
  print('Private signed Object-trial image prepared. No hardware changed.')


if __name__ == '__main__':
  main()
