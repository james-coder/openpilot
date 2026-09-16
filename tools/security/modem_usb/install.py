"""Operator-only installation of the candidate authorizer; does not start it.

Run only after a fresh offroad gate and review. The unit is skipped on the
known-good kernel (no DT marker), and activated at boot only when the candidate
kernel advertises the physical-port policy. Installation is NOT flash approval.
No USB authorization flags, drivers, modem state or boot partitions are changed.
"""
import os
from contextlib import suppress
from pathlib import Path
import shutil
import stat
import subprocess

ROOT = Path('/usr/local/lib/comma-modem-usb')
UNIT = Path('/etc/systemd/system/comma-modem-usb.service')


def trusted_directory(path):
  for parent in (path, *path.parents):
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
      raise RuntimeError('Untrusted installation directory')


def run(argv):
  subprocess.run(argv, check=True, timeout=15, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})


def install(source):
  if os.geteuid() != 0:
    raise RuntimeError('Root required')
  for path in (ROOT, UNIT):
    if path.exists() or path.is_symlink():
      raise RuntimeError('Refusing existing installation')
    trusted_directory(path.parent)
  # Snapshot reviewed input before remounting; imports are stdlib only.
  script = (source / 'authorize.py').read_bytes()
  unit = (source / UNIT.name).read_bytes()
  compile(script, 'authorize.py', 'exec')
  if b'ConditionPathExists=/sys/firmware/devicetree/base/soc/ssusb@a800000/comma,untrusted-modem-host\n' not in unit:
    raise RuntimeError('Missing candidate-kernel gate')
  readonly = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
  created_root = created_unit = False
  if readonly:
    run(['/usr/bin/mount', '-o', 'remount,rw', '/'])
  try:
    ROOT.mkdir(mode=0o755)
    created_root = True
    with (ROOT / 'authorize.py').open('xb') as stream:
      stream.write(script)
    (ROOT / 'authorize.py').chmod(0o644)
    with UNIT.open('xb') as stream:
      created_unit = True
      stream.write(unit)
    UNIT.chmod(0o644)
    run(['/usr/bin/systemd-analyze', 'verify', str(UNIT)])
    run(['/usr/bin/systemctl', 'daemon-reload'])
    # No --now: a known-good kernel must not run a late userspace allowlist.
    run(['/usr/bin/systemctl', 'enable', UNIT.name])
    os.sync()
  except Exception:
    # Only paths created by THIS invocation; never remove a prior install.
    if created_unit:
      with suppress(OSError, subprocess.SubprocessError):
        run(['/usr/bin/systemctl', 'disable', UNIT.name])
      UNIT.unlink()
    if created_root:
      shutil.rmtree(ROOT)
    with suppress(OSError, subprocess.SubprocessError):
      run(['/usr/bin/systemctl', 'daemon-reload'])
    raise
  finally:
    if readonly:
      run(['/usr/bin/mount', '-o', 'remount,ro', '/'])


if __name__ == '__main__':
  install(Path(__file__).resolve().parent)
