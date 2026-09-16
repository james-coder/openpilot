"""Persistent cellular firewall trial controller, installed in a root-owned tree.

No installer and no automatic confirmation. The operator must provision filter
snapshots and phase=pending before enabling the supplied units. /etc is on the
read-only AGNOS root; phase updates temporarily remount it and restore its mode.
Never run alongside another firewall administrator during a pending trial.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess

spec = importlib.util.spec_from_file_location('firewall_trial', Path(__file__).with_name('firewall_trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)

STATE = Path('/etc/comma-cellular-firewall')
TIMER = 'comma-cellular-rollback.timer'


def run(argv, **kwargs):
  return subprocess.run(argv, check=True, timeout=20, **kwargs)


@contextmanager
def writable_root():
  was_readonly = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
  if was_readonly:
    run(['/usr/bin/mount', '-o', 'remount,rw', '/'])
  try:
    yield
  finally:
    os.sync()
    if was_readonly:
      run(['/usr/bin/mount', '-o', 'remount,ro', '/'])


def phase():
  trial.trusted(STATE / 'phase')
  value = (STATE / 'phase').read_text().strip()
  if value not in ('pending', 'confirmed', 'rollback', 'restored'):
    raise RuntimeError('Invalid persistent firewall phase')
  return value


def set_phase(value):
  with writable_root():
    temporary = STATE / 'phase.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
      f.write(value + '\n')
      f.flush()
      os.fsync(f.fileno())
    temporary.replace(STATE / 'phase')
    # Directory entry durability matters across an unexpected power loss.
    fd = os.open(STATE, os.O_DIRECTORY)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)


def stop_timer():
  run(['/usr/bin/systemctl', 'stop', TIMER])


def restore():
  if phase() in ('confirmed', 'restored'):
    stop_timer()
    return
  # Persist this BEFORE restoration: after reboot boot() must not reapply.
  if phase() != 'rollback':
    set_phase('rollback')
  errors = []
  for family in trial.FAMILIES:
    try:
      snapshot = STATE / family
      trial.trusted(snapshot)
      saved = snapshot.read_text()
      run([f'/usr/sbin/{family}-legacy-restore'], input=saved, text=True)
      actual = subprocess.check_output([f'/usr/sbin/{family}-legacy-save', '-t', 'filter'], text=True, timeout=15)
      if trial.rules_only(actual) != trial.rules_only(saved):
        raise RuntimeError('Restored filter table differs from snapshot')
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
      errors.append(exc)
  if errors:
    raise RuntimeError('Incomplete rollback; independent timer will retry') from errors[0]
  set_phase('restored')
  stop_timer()


def boot():
  current = phase()
  if current in ('rollback', 'restored'):
    restore()
    return
  if current == 'pending':
    # Wants/After ordering alone is insufficient; explicitly check activation.
    run(['/usr/bin/systemctl', 'is-active', '--quiet', TIMER])
  else:
    stop_timer()
  try:
    trial.firewall.apply()
  except BaseException:
    if current == 'pending':
      restore()
    raise


def confirm():
  if phase() != 'pending':
    raise RuntimeError('No pending trial')
  trial.firewall.verify()
  # Connectivity/manager tests and successful new-boot verification must be
  # performed by the operator first; rule validation alone cannot prove them.
  set_phase('confirmed')
  stop_timer()


def rearm():
  if phase() != 'restored':
    raise RuntimeError('Rearm requires a completed rollback')
  for family in trial.FAMILIES:
    snapshot = STATE / family
    trial.trusted(snapshot)
    actual = subprocess.check_output([f'/usr/sbin/{family}-legacy-save', '-t', 'filter'], text=True, timeout=15)
    if trial.rules_only(actual) != trial.rules_only(snapshot.read_text()):
      raise RuntimeError('Baseline changed; refusing stale-snapshot trial')
  set_phase('pending')
  run(['/usr/bin/systemctl', 'restart', TIMER])
  run(['/usr/bin/systemctl', 'is-active', '--quiet', TIMER])


def main(action):
  if os.geteuid() != 0 or os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'):
    raise RuntimeError('Root in the host network namespace required')
  for path in (Path(__file__), Path(trial.__file__), Path(trial.firewall.__file__), STATE):
    trial.trusted(path)
  # /run is root-owned and reset at boot, unlike the persistent phase/snapshots.
  trial.trusted(Path('/run'))
  fd = os.open('/run/comma-cellular-firewall.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    globals()[action]()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('boot', 'restore', 'confirm', 'rearm'))
  main(parser.parse_args().action)
