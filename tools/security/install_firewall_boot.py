"""One-time, operator-gated installation; never invoked by openpilot.

Run only after a fresh offroad check. Refuses existing installation/state.
Copies reviewed sources into a root-controlled tree before any root callback.
Does not start/apply the firewall: operator must separately gate that step.
"""
import os
from pathlib import Path
import shutil
import subprocess


def run(argv, **kwargs):
  return subprocess.run(argv, check=True, timeout=30, **kwargs)


def install(source):
  target = Path('/usr/local/lib/comma-cellular')
  state = Path('/etc/comma-cellular-firewall')
  units = ('comma-cellular-firewall.service', 'comma-cellular-rollback.service', 'comma-cellular-rollback.timer')
  files = ('system/hardware/tici/cellular_firewall.py', 'tools/security/firewall_trial.py', 'tools/security/firewall_boot.py')
  destinations = [target, state, *[Path('/etc/systemd/system') / name for name in units]]
  if os.geteuid() != 0 or any(p.exists() or p.is_symlink() for p in destinations):
    raise RuntimeError('Root required; refusing existing installation')
  for name in (*files, *['tools/security/systemd/' + u for u in units]):
    if not (source / name).is_file():
      raise RuntimeError('Missing staged source: ' + name)
  snapshots = {}
  for family in ('iptables', 'ip6tables'):
    saved = subprocess.check_output([f'/usr/sbin/{family}-legacy-save', '-t', 'filter'], text=True, timeout=15)
    if 'COMMA_CELL_' in saved:
      raise RuntimeError('Refusing baseline containing candidate policy')
    run([f'/usr/sbin/{family}-legacy-restore', '--test'], input=saved, text=True)
    snapshots[family] = saved
  was_readonly = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
  if was_readonly:
    run(['/usr/bin/mount', '-o', 'remount,rw', '/'])
  try:
    for name in files:
      destination = target / name
      destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
      shutil.copyfile(source / name, destination)
      destination.chmod(0o644)
    state.mkdir(mode=0o700)
    for name, saved in snapshots.items():
      (state / name).write_text(saved)
      (state / name).chmod(0o600)
    (state / 'phase').write_text('pending\n')
    (state / 'phase').chmod(0o600)
    for name in units:
      destination = Path('/etc/systemd/system') / name
      shutil.copyfile(source / 'tools/security/systemd' / name, destination)
      destination.chmod(0o644)
    run(['/usr/bin/systemd-analyze', 'verify', *[str(Path('/etc/systemd/system') / u) for u in units]])
    os.sync()
    run(['/usr/bin/systemctl', 'daemon-reload'])
    run(['/usr/bin/systemctl', 'enable', '--now', 'comma-cellular-rollback.timer'])
    run(['/usr/bin/systemctl', 'is-active', '--quiet', 'comma-cellular-rollback.timer'])
    # Only enable boot application once a durable independent timer is armed.
    run(['/usr/bin/systemctl', 'enable', 'comma-cellular-firewall.service'])
    os.sync()
  finally:
    if was_readonly:
      run(['/usr/bin/mount', '-o', 'remount,ro', '/'])
  print('Installed pending trial and armed rollback. Firewall NOT started/applied.')


if __name__ == '__main__':
  install(Path(__file__).resolve().parents[2])
