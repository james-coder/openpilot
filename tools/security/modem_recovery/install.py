"""Operator-only broker installation after a separate fresh offroad gate.

Does not restart any process/modem. Refuses an existing installation. To roll
back, rename /usr/local/lib/comma-modem out of that path; ordinary PPP retries
continue without the optional broker. No systemd/watchdog changes.
"""
import os
from pathlib import Path
import shutil
import subprocess


def install(source, target=Path('/usr/local/lib/comma-modem')):
  if os.geteuid() != 0 or target.exists() or target.is_symlink():
    raise RuntimeError('Root required; refusing existing installation')
  files = ('check_offroad.py', 'recover.py')  # broker appears LAST
  for name in files:
    compile((source / name).read_bytes(), name, 'exec')
  readonly = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
  if readonly:
    subprocess.run(['/usr/bin/mount', '-o', 'remount,rw', '/'], check=True, timeout=10)
  try:
    target.mkdir(mode=0o755)
    for name in files:
      shutil.copyfile(source / name, target / name)
      (target / name).chmod(0o644)
    os.sync()
  finally:
    if readonly:
      subprocess.run(['/usr/bin/mount', '-o', 'remount,ro', '/'], check=True, timeout=10)


if __name__ == '__main__':
  install(Path(__file__).resolve().parent)
