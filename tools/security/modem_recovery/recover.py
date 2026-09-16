"""Fixed-operation, once-per-host-boot LTE reset broker. Not installed by default.

Install this tree root-owned at /usr/local/lib/comma-modem only after validation.
No user arguments, shell, environment-derived paths, or root openpilot imports.
The normal safety observer runs as comma, not root. This does NOT remove the
existing comma user's unrestricted sudo or claim isolation from that user.
"""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path('/usr/local/lib/comma-modem')
STATE = Path('/run/comma-modem-recovery')


def trusted(path):
  for item in (path, *path.parents):
    info = item.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
      raise RuntimeError('Untrusted broker path')


def safe():
  result = subprocess.run(['/usr/sbin/runuser', '-u', 'comma', '--', '/usr/local/venv/bin/python',
                           '-I', str(ROOT / 'check_offroad.py')],
                          cwd='/', env={'PATH': '/usr/bin:/bin', 'LANG': 'C'},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
  return result.returncode == 0


def recover():
  if os.geteuid() != 0:
    return 2
  trusted(ROOT / 'recover.py')
  trusted(ROOT / 'check_offroad.py')
  trusted(STATE.parent)
  STATE.mkdir(mode=0o700, exist_ok=True)
  trusted(STATE)
  fd = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'a') as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      return 2
    if (STATE / 'attempted').exists():
      return 3
    if not safe():
      return 2
    # Consume budget BEFORE asking PID 1; crash, denial and timeout cannot loop.
    fd = os.open(STATE / 'attempted', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    subprocess.run(['/usr/bin/systemctl', '--no-block', 'restart', 'lte.service'],
                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C'},
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
    return 0  # request accepted, NOT proof that registration/data recovered


if __name__ == '__main__':
  if len(sys.argv) != 1:
    sys.exit(2)
  try:
    sys.exit(recover())
  except (OSError, RuntimeError, subprocess.SubprocessError):
    sys.exit(2)
