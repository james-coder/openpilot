"""Lab-only timed firewall trial; not a production installer.

Run from a root-owned installation in a disposable systemd VM. The timer is
owned by PID 1, not the caller/SSH session. Snapshots cover filter tables only.
Do not run concurrent firewall administrators: rollback restores the snapshot.
No command in this tool contacts the comma or installs a boot policy.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import hashlib
import os
from pathlib import Path
import stat
import re
import subprocess
import sys

# Load the adjacent installed source tree explicitly: the timer must not rely
# on the invoking shell's PYTHONPATH or a developer venv's editable packages.
spec = importlib.util.spec_from_file_location('cellular_firewall', Path(__file__).resolve().parents[2] /
                                            'system/hardware/tici/cellular_firewall.py')
firewall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firewall)

FAMILIES = ('iptables', 'ip6tables')
UNIT = 'comma-cellular-lab-rollback'


def unit(directory):
  return UNIT + '-' + hashlib.sha256(str(directory).encode()).hexdigest()[:12]


def check_namespace(directory):
  if (directory / 'namespace').read_text() != os.readlink('/proc/self/ns/net'):
    raise RuntimeError('Refusing to restore/confirm in a different network namespace')


def rules_only(saved):
  return [re.sub(r'\[\d+:\d+\]', '[0:0]', line) for line in saved.splitlines() if line and not line.startswith('#')]


def run(argv, **kwargs):
  return subprocess.run(argv, check=True, timeout=15, **kwargs)


def trusted(path):
  path = Path(path).absolute()
  for item in (path, *path.parents):
    st = item.lstat()
    if st.st_uid != 0 or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
      raise RuntimeError(f'Root-owned, non-writable, non-symlink path required: {item}')


@contextmanager
def locked(directory):
  trusted(directory)
  with (directory / 'lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    yield


def restore(directory):
  with locked(directory):
    check_namespace(directory)
    phase = directory / 'phase'
    if phase.read_text() in ('confirmed', 'restored'):
      return
    errors = []
    # Attempt the second family even if the first fails. The repeating timer
    # remains armed after any failure, including a killed restore process.
    for family in FAMILIES:
      try:
        snapshot = directory / family
        trusted(snapshot)
        run([f'/usr/sbin/{family}-legacy-restore'], input=snapshot.read_text(), text=True)
        actual = subprocess.check_output([f'/usr/sbin/{family}-legacy-save', '-t', 'filter'], text=True, timeout=15)
        if rules_only(actual) != rules_only(snapshot.read_text()):
          raise RuntimeError('Restored rules do not match snapshot')
      except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        errors.append(exc)
    if errors:
      raise RuntimeError('Rollback incomplete; timer remains armed') from errors[0]
    phase.write_text('restored')
  run(['systemctl', 'stop', unit(directory) + '.timer'])


def trial(directory, seconds=180, network_namespace=None):
  if not 120 <= seconds <= 600:
    raise ValueError('Trial timeout must be 120–600 seconds')
  # Root-controlled code/config is mandatory for a root PID-1 callback.
  for path in (Path(__file__), Path(firewall.__file__), Path(sys.executable).resolve()):
    trusted(path)
  trusted(directory.parent)
  namespace_options = []
  if network_namespace is not None:
    trusted(network_namespace)
    if os.stat(network_namespace).st_ino != os.stat('/proc/self/ns/net').st_ino:
      raise RuntimeError('Trial must run inside the specified network namespace')
    namespace_options = ['--property=NetworkNamespacePath=' + str(network_namespace)]
  elif os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'):
    raise RuntimeError('Explicit network namespace required for isolated trial')
  directory.mkdir(mode=0o700)
  (directory / 'namespace').write_text(os.readlink('/proc/self/ns/net'))
  for family in FAMILIES:
    saved = subprocess.check_output([f'/usr/sbin/{family}-legacy-save', '-t', 'filter'], text=True, timeout=15)
    run([f'/usr/sbin/{family}-legacy-restore', '--test'], input=saved, text=True)
    (directory / family).write_text(saved)
  (directory / 'phase').write_text('pending')
  # --on-unit-active retries a failed restore. A successful restore stops it.
  run(['systemd-run', '--unit=' + unit(directory), f'--on-active={seconds}s', '--on-unit-active=30s',
       '--timer-property=AccuracySec=1s', '--property=TimeoutStartSec=60s',
       '--property=UMask=0077', *namespace_options, '--', str(Path(sys.executable).resolve()),
       str(Path(__file__).absolute()), 'restore', str(directory)])
  run(['systemctl', 'is-active', '--quiet', unit(directory) + '.timer'])
  # No apply is possible before both the snapshots and independent timer exist.
  try:
    with locked(directory):
      if (directory / 'phase').read_text() != 'pending':
        raise RuntimeError('Trial already expired')
      firewall.apply()
  except BaseException:
    restore(directory)
    raise


def confirm(directory):
  # Explicit operator action only AFTER separate connectivity tests. Rule
  # verification is necessary, but it cannot prove DNS/HTTPS/SSH connectivity.
  with locked(directory):
    check_namespace(directory)
    if (directory / 'phase').read_text() != 'pending':
      raise RuntimeError('No pending trial to confirm')
    firewall.verify()
    (directory / 'phase').write_text('confirmed')
  run(['systemctl', 'stop', unit(directory) + '.timer'])


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('trial', 'restore', 'confirm'))
  parser.add_argument('directory', type=Path)
  parser.add_argument('--network-namespace', type=Path, help='Named namespace path; trial must already be running inside it')
  args = parser.parse_args()
  if os.geteuid() != 0:
    parser.error('Disposable root-owned lab installation required')
  if args.action == 'trial':
    trial(args.directory.absolute(), network_namespace=args.network_namespace)
  else:
    globals()[args.action](args.directory.absolute())
