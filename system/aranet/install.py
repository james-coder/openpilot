"""Explicit parked installation; no launch/manager hook and no firmware flashing.

sudo /usr/local/venv/bin/python -m openpilot.system.aranet.install
Uses already provisioned, hash-pinned device assets; never downloads at boot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess

from openpilot.common.basedir import BASEDIR
from openpilot.system.aranet.firmware import HASHES
from openpilot.system.aranet.protocol import ASSETS
from openpilot.system.aranet.safety import offroad

BIN_HASHES = {
  'hciattach': '65f9dce15d4385c4b3db0478ac83f8154d192290d304868a4b268fea563af733',
  'hcitool': '24255a614ebfe69b4282b0f1ff9fc1aeb11844175de5458cc8b27ce5c5d17ee1',
  'hciconfig': 'f1de33bc8bfcdfa65d95c8c6b59d4d3e8e2873f1337c282eb7e5e62f8418f561',
  'btmgmt': 'f67995fdf60cd9a44fe4c74c8338d7aba410eef219cf3719efa7e23f438472e3',
}


def regular(path):
  if not stat.S_ISREG(path.lstat().st_mode):
    raise RuntimeError(f'Refusing non-regular file: {path}')


def directory(path):
  if path.is_symlink():
    raise RuntimeError(f'Refusing symlink directory: {path}')
  path.mkdir(exist_ok=True, mode=0o755)
  if not path.is_dir():
    raise RuntimeError(f'Not a directory: {path}')


def verify_assets(source):
  entries = []
  for sub, hashes in [('bin', BIN_HASHES), ('firmware', HASHES)]:
    for name, expected in hashes.items():
      path = source / ('usr/bin' if sub == 'bin' else sub) / name
      regular(path)
      if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f'Asset checksum mismatch: {name}')
      entries.append((path, sub, name, expected))
  return entries


def migrate_history(root, uid, gid):
  directory(root)
  os.chown(root, uid, gid)
  for name in ('collector.lock', 'history.sqlite', 'history.sqlite-journal', 'status.json', 'status.tmp', 'paused'):
    path = root / name
    if path.exists() or path.is_symlink():
      regular(path)
      if path.stat().st_nlink != 1:
        raise RuntimeError(f'Refusing multiply linked file: {path}')
      os.chown(path, uid, gid)
      os.chmod(path, 0o644)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--assets', type=Path, default=Path('/data/bluetooth-test'))
  parser.add_argument('--disable', action='store_true', help='Stop optional services only; retain history and installed fix')
  args = parser.parse_args()
  if os.geteuid() != 0:
    raise RuntimeError('Installer requires root')
  offroad()
  units = ['aranet.service', 'aranet-bluetooth.service']
  if args.disable:
    subprocess.run(['systemctl', 'disable', '--now', *units], check=True)
    return
  entries = verify_assets(args.assets)
  # Old manager registration must be removed and manager restarted before installation.
  from cereal import messaging
  sm = messaging.SubMaster(['managerState'])
  for _ in range(30):
    sm.update(100)
    if sm.seen['managerState']:
      break
  if not sm.seen['managerState'] or not sm.valid['managerState']:
    raise RuntimeError('Fresh manager state required for migration')
  if any(p.name == 'aranetd' and p.running for p in sm['managerState'].processes):
    raise RuntimeError('Old manager collector still running; restart parked with new configuration first')
  offroad()
  subprocess.run(['systemctl', 'stop', *units], check=False)
  directory(ASSETS)
  os.chown(ASSETS, 0, 0)
  for sub in ('bin', 'firmware'):
    directory(ASSETS / sub)
    os.chown(ASSETS / sub, 0, 0)
  for source, sub, name, _expected in entries:
    target = ASSETS / sub / name
    if target.exists() or target.is_symlink():
      regular(target)
    shutil.copyfile(source, target)
    os.chown(target, 0, 0)
    os.chmod(target, 0o755 if sub == 'bin' else 0o644)
  (ASSETS / 'manifest.json').write_text(json.dumps(dict(binary_sha256=BIN_HASHES, firmware_sha256=HASHES), indent=2))
  user = pwd.getpwnam('comma')
  migrate_history(Path('/data/aranet'), user.pw_uid, user.pw_gid)
  for source, name in [('system/manager/aranet.service', units[0]), ('system/aranet/aranet-bluetooth.service', units[1])]:
    target = Path('/etc/systemd/system') / name
    if target.exists() or target.is_symlink():
      regular(target)
    shutil.copyfile(Path(BASEDIR) / source, target)
    os.chmod(target, 0o644)
  offroad()
  subprocess.run(['systemctl', 'daemon-reload'], check=True)
  subprocess.run(['systemctl', 'enable', '--now', *units], check=True)


if __name__ == '__main__':
  main()
