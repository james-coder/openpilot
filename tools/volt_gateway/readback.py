"""Main-flash upload ONLY from the identified labeled Panda's STM32 ROM DFU.

No entry/reset, download, erase, unprotect or option-byte operations. Output is a
new private directory outside the repository. No caller-supplied tool flags.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from openpilot.tools.volt_gateway.backup import dfuse_geometry, linux_read_command, verify_files


DFU_SERIAL = '365236793036'
SERIAL = '370022000651363038363036'


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True, help='new directory under /home/james/diagnostics/volt-gateway/backups')
  args = parser.parse_args()
  root = Path('/home/james/diagnostics/volt-gateway/backups')
  output = args.output
  if not output.is_absolute() or output.parent != root or output.exists() or output.is_symlink() or '..' in output.parts:
    raise ValueError('invalid/occupied private backup directory')
  os.umask(0o077)
  root.mkdir(mode=0o700, exist_ok=True)
  if root.is_symlink() or root.stat().st_mode & 0o077:
    raise ValueError('backup root must be private and not a symlink')
  listed = subprocess.run(['/usr/bin/dfu-util', '-l'], capture_output=True, text=True, timeout=15, check=True)
  lines = [line for line in listed.stdout.splitlines() if 'alt=0,' in line and f'serial="{DFU_SERIAL}"' in line
           and '[0483:df11]' in line]
  if len(lines) != 1:
    raise RuntimeError('expected one exact STM32 DFU device')
  descriptor = re.search(r'name="([^"]+)"', lines[0])[1]
  extent = dfuse_geometry(descriptor)
  output.mkdir(mode=0o700)
  (output / 'dfu-list.txt').write_text(listed.stdout + listed.stderr)
  print(json.dumps({'stage': 'identified', 'dfu_serial': DFU_SERIAL, 'flash_bytes': extent}), flush=True)
  paths = []
  for number in (1, 2):
    destination = output / f'flash-read-{number}.bin'
    argv = linux_read_command(DFU_SERIAL, extent, destination)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    (output / f'read-{number}.log').write_text(result.stdout + result.stderr)
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size != extent:
      raise RuntimeError('Read failed/incomplete. STOP: do not unprotect or erase. Private log: ' + str(output))
    paths.append(destination)
    print(json.dumps({'stage': 'read_complete', 'read': number, 'bytes': extent}), flush=True)
  report = verify_files(*paths, extent)
  image = paths[0].read_bytes()
  app = 0x4000
  length = int.from_bytes(image[app:app + 4], 'little')
  if not 8 <= length <= extent - app - 128:
    raise RuntimeError('Backup read twice, but application layout differs; retain dumps and investigate')
  inventory_path = Path(__file__).resolve().parents[2] / 'docs/evidence/volt-gateway/inventory-20260916.json'
  inventory = json.loads(inventory_path.read_text())
  digest = hashlib.sha1(image[app + 4:app + length]).hexdigest()
  signature_match = image[app + length:app + length + 128].hex() == inventory['application_signature_hex']
  digest_match = digest == inventory['historical_release_key_signed_sha1']
  report.update({'serial': SERIAL, 'dfu_serial': DFU_SERIAL, 'descriptor': descriptor, 'application_length': length,
                 'application_signed_sha1': digest, 'application_digest_matches_prior_signature': digest_match,
                 'application_signature_matches_prior_read': signature_match,
                 'programming_performed': False})
  (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
  if not signature_match or not digest_match:
    raise RuntimeError('Double read preserved, but prior signature match failed; do not flash')
  print(json.dumps({'stage': 'verified', 'output': str(output), **report}, indent=2), flush=True)


if __name__ == '__main__':
  main()
