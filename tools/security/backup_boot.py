"""Read-only SSH backup of both current boot slots; no flashing or reboot.

Requires fresh offroad telemetry before reading partitions. Saves exclusive new
files locally; compares each streamed backup against a remote SHA256 after read.
An image backup does not establish a working bootloader recovery procedure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def backup(ssh, destination):
  destination.mkdir(mode=0o700, parents=True, exist_ok=False)
  gate = 'cd /data/openpilot && /usr/local/venv/bin/python -c "from openpilot.system.aranet.safety import offroad; offroad()"'
  subprocess.run([*ssh, gate], check=True, timeout=15)
  metadata = subprocess.check_output([*ssh, 'uname -a; cat /VERSION; cat /proc/cmdline; cd /data/openpilot && git rev-parse HEAD'],
                                     text=True, timeout=15)
  entries = {}
  for slot in ('a', 'b'):
    subprocess.run([*ssh, gate], check=True, timeout=15)
    partition = f'/dev/disk/by-partlabel/boot_{slot}'
    path = destination / f'boot_{slot}.img'
    digest, size = hashlib.sha256(), 0
    with path.open('xb') as output:
      child = subprocess.Popen([*ssh, f'sudo dd if={partition} bs=1M status=none'], stdout=subprocess.PIPE)
      try:
        while chunk := child.stdout.read(1024 * 1024):
          size += len(chunk)
          if size > 128 * 1024 * 1024:
            raise RuntimeError('Unexpected boot partition size')
          digest.update(chunk)
          output.write(chunk)
      finally:
        child.stdout.close()
        if child.poll() is None and size > 128 * 1024 * 1024:
          child.terminate()
      if child.wait(timeout=15):
        raise RuntimeError('Incomplete backup; do not use image')
    remote = subprocess.check_output([*ssh, f'sudo sha256sum {partition}'], text=True, timeout=30).split()[0]
    if size == 0 or digest.hexdigest() != remote:
      raise RuntimeError('Backup mismatch; do not use image')
    entries[slot] = dict(bytes=size, sha256=remote)
  with (destination / 'manifest.json').open('x') as output:
    json.dump(dict(metadata=metadata, images=entries, verified=True), output, indent=2)
  print(json.dumps(entries, indent=2))


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--destination', type=Path, required=True)
  parser.add_argument('--host', default='comma@192.168.98.187')
  parser.add_argument('--host-key-alias', default='192.168.0.106')
  args = parser.parse_args()
  backup(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
          '-o', f'HostKeyAlias={args.host_key_alias}', args.host], args.destination)
