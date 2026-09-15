"""Explicit offroad-only installation of the independent thermal recorder."""
import os
from pathlib import Path
import pwd
import shutil
import subprocess

from openpilot.common.basedir import BASEDIR
from openpilot.system.aranet.install import directory, regular, writable_root
from openpilot.system.aranet.safety import offroad
from openpilot.system.hardware.thermal_history import ROOT


def main():
  if os.geteuid() != 0:
    raise RuntimeError('Installer requires root')
  offroad()
  unit = 'thermal-history.service'
  subprocess.run(['systemctl', 'stop', unit], check=False)
  directory(ROOT)
  user = pwd.getpwnam('comma')
  os.chown(ROOT, user.pw_uid, user.pw_gid)
  os.chmod(ROOT, 0o755)
  for name in ('collector.lock', 'summary.json', 'summary.tmp', 'timeline.sqlite', 'timeline.sqlite-journal'):
    path = ROOT / name
    if path.exists() or path.is_symlink():
      regular(path)
      if path.stat().st_nlink != 1:
        raise RuntimeError('Refusing multiply linked history file')
      os.chown(path, user.pw_uid, user.pw_gid)
  offroad()
  with writable_root():
    target = Path('/etc/systemd/system') / unit
    if target.exists() or target.is_symlink():
      regular(target)
    shutil.copyfile(Path(BASEDIR) / 'system/hardware' / unit, target)
    os.chmod(target, 0o644)
    subprocess.run(['systemctl', 'enable', unit], check=True)
  subprocess.run(['systemctl', 'daemon-reload'], check=True)
  offroad()
  subprocess.run(['systemctl', 'start', unit], check=True)


if __name__ == '__main__':
  main()
