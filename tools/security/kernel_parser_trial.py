"""Fixed boot_a trial, with independent boot-surviving timed rollback.

Cannot recover a pre-userspace hang. Rollback refuses to write/reboot without
fresh safe telemetry; it retries later rather than guessing the car is parked.
Root callbacks import only stdlib and operate only on pinned boot partitions.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

ROOT = Path('/usr/local/lib/comma-kernel-parser-trial')
TARGET = Path('/dev/disk/by-partlabel/boot_a')
OTHER = Path('/dev/disk/by-partlabel/boot_b')
SIZE = 67108864
BASELINE = '46fd613ff1b1147ad0212d205fc96181fb7b25dbdcb273210882a64efb56cdbd'
OTHER_HASH = 'bf0dd9ff2393131dfa7c6dac40af7ae709a858f588755076502e4b9996b6afd1'
TIMER = 'comma-kernel-parser-rollback.timer'
UNIT = 'comma-kernel-parser-rollback.service'
SERVICE_TEXT = '''[Unit]
Description=Guarded rollback of unconfirmed experimental kernel
After=local-fs.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/lib/comma-kernel-parser-trial/kernel_parser_trial.py rollback
Restart=on-failure
RestartSec=30
TimeoutStartSec=120
'''
TIMER_TEXT = '''[Unit]
Description=Independent experimental kernel rollback deadline
[Timer]
OnActiveSec=8min
AccuracySec=1s
Unit=comma-kernel-parser-rollback.service
[Install]
WantedBy=timers.target
'''


def run(argv, **kwargs):
  return subprocess.run(argv, check=True, timeout=30, **kwargs)


def trusted(path):
  for p in (path, *path.parents):
    s = p.lstat()
    if s.st_uid != 0 or s.st_mode & 0o022 or stat.S_ISLNK(s.st_mode):
      raise RuntimeError('Untrusted root callback/data path')


def digest(path):
  h = hashlib.sha256()
  with path.open('rb') as f:
    while data := f.read(1024 * 1024):
      h.update(data)
  return h.hexdigest()


@contextmanager
def writable():
  readonly = bool(os.statvfs('/').f_flag & os.ST_RDONLY)
  if readonly:
    run(['/usr/bin/mount', '-o', 'remount,rw', '/'])
  try:
    yield
  finally:
    os.sync()
    if readonly:
      run(['/usr/bin/mount', '-o', 'remount,ro', '/'])


def safe():
  observer = Path('/usr/local/lib/comma-modem/check_offroad.py')
  trusted(observer)
  run(['/usr/sbin/runuser', '-u', 'comma', '--', '/usr/local/venv/bin/python', '-I', str(observer)],
      cwd='/', env={'PATH': '/usr/bin:/bin', 'LANG': 'C'}, capture_output=True)


def hardware():
  if TARGET.resolve() != Path('/dev/sde11') or OTHER.resolve() != Path('/dev/sde28'):
    raise RuntimeError('Unexpected boot partition mapping')
  if not stat.S_ISBLK(TARGET.stat().st_mode):
    raise RuntimeError('Target is not a block device')
  slot = run(['/usr/sbin/abctl', '--boot_slot'], capture_output=True, text=True).stdout.strip()
  if slot != '_a' or digest(OTHER) != OTHER_HASH:
    raise RuntimeError('Boot slot/baseline changed')


def phase():
  trusted(ROOT / 'phase')
  return (ROOT / 'phase').read_text().strip()


def set_phase(value):
  with writable():
    path = ROOT / 'phase.new'
    with path.open('w') as f:
      f.write(value + '\n')
      f.flush()
      os.fsync(f.fileno())
    path.replace(ROOT / 'phase')
    fd = os.open(ROOT, os.O_DIRECTORY)
    try:
      os.fsync(fd)
    finally:
      os.close(fd)


def manifest():
  trusted(ROOT / 'manifest.json')
  m = json.loads((ROOT / 'manifest.json').read_text())
  for name, expected in [('baseline.img', BASELINE), ('candidate.img', m['candidate_sha256'])]:
    trusted(ROOT / name)
    if digest(ROOT / name) != expected:
      raise RuntimeError('Boot image digest mismatch')
  return m


def write_image(source):
  # Safety/mapping/hash checks must precede this single fixed-target sink.
  with source.open('rb') as src, TARGET.open('r+b', buffering=0) as dst:
    shutil.copyfileobj(src, dst, 1024 * 1024)
    os.fsync(dst.fileno())


def flash():
  m = manifest()
  hardware()
  if phase() != 'prepared' or digest(TARGET) != BASELINE:
    raise RuntimeError('Not a pristine prepared trial')
  run(['/usr/bin/systemctl', 'stop', TIMER])
  with writable():
    run(['/usr/bin/systemctl', 'enable', '--now', TIMER])
  run(['/usr/bin/systemctl', 'is-active', '--quiet', TIMER])
  safe()
  set_phase('writing')
  write_image(ROOT / 'candidate.img')
  if digest(TARGET) != m['partition_sha256'] or digest(OTHER) != OTHER_HASH:
    raise RuntimeError('Readback mismatch; rollback remains armed')
  set_phase('pending')
  print('Candidate boot_a readback verified; boot_b unchanged; rollback armed. No reboot yet.')


def rollback():
  current = phase()
  if current == 'restored':
    trusted(ROOT / 'restore_boot_id')
  if current == 'restored' and (ROOT / 'restore_boot_id').read_text().strip() == boot_id():
    hardware()
    if digest(TARGET) != BASELINE:
      raise RuntimeError('Restored partition changed')
    safe()
    run(['/usr/bin/systemctl', '--no-block', 'reboot'])
    return
  if current in ('confirmed', 'restored', 'cancelled'):
    run(['/usr/bin/systemctl', 'stop', TIMER])
    return
  m = manifest()
  hardware()
  actual = digest(TARGET)
  if actual == BASELINE:
    set_phase('cancelled')
    run(['/usr/bin/systemctl', 'stop', TIMER])
    return
  if current not in ('writing', 'restoring') and actual != m['partition_sha256']:
    raise RuntimeError('Unknown image; refusing overwrite')
  safe()
  with writable():
    (ROOT / 'restore_boot_id').write_text(boot_id() + '\n')
  set_phase('restoring')
  write_image(ROOT / 'baseline.img')
  if digest(TARGET) != BASELINE or digest(OTHER) != OTHER_HASH:
    raise RuntimeError('Baseline restore verification failed')
  set_phase('restored')
  run(['/usr/bin/systemctl', 'stop', TIMER])
  # Recheck immediately before requesting reboot; never reboot onroad.
  safe()
  run(['/usr/bin/systemctl', '--no-block', 'reboot'])


def boot_id():
  return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def confirm():
  # Operator invokes only after separate benign compatibility checks.
  m = manifest()
  hardware()
  safe()
  trusted(ROOT / 'start_boot_id')
  if boot_id() == (ROOT / 'start_boot_id').read_text().strip() or os.uname().version != m['kernel_version']:
    raise RuntimeError('Candidate kernel has not booted')
  marker = Path('/sys/firmware/devicetree/base/soc/ssusb@a800000/comma,untrusted-modem-host')
  if phase() != 'pending' or digest(TARGET) != m['partition_sha256'] or not marker.exists():
    raise RuntimeError('Not a running candidate trial')
  run(['/usr/bin/systemctl', 'is-active', '--quiet', 'comma-modem-usb.service'])
  set_phase('confirmed')
  with writable():
    run(['/usr/bin/systemctl', 'disable', '--now', TIMER])


def rearm():
  manifest()
  hardware()
  safe()
  if phase() != 'cancelled' or digest(TARGET) != BASELINE:
    raise RuntimeError('Only an unwritten cancelled preflight may be rearmed')
  set_phase('prepared')


def install(source, expected, kernel_version):
  if not kernel_version.startswith('#') or len(kernel_version) > 160 or '\n' in kernel_version:
    raise RuntimeError('Invalid expected kernel version')
  if ROOT.exists() or ROOT.is_symlink():
    raise RuntimeError('Refusing existing trial')
  trusted(ROOT.parent)
  for name in (UNIT, TIMER):
    p = Path('/etc/systemd/system') / name
    if p.exists() or p.is_symlink():
      raise RuntimeError('Existing rollback unit')
  safe()
  hardware()
  candidate = (source / 'candidate.img').read_bytes()
  if not 5_000_000 < len(candidate) < SIZE or hashlib.sha256(candidate).hexdigest() != expected:
    raise RuntimeError('Candidate digest/size mismatch')
  baseline = TARGET.read_bytes()
  if len(baseline) != SIZE or hashlib.sha256(baseline).hexdigest() != BASELINE:
    raise RuntimeError('Live baseline mismatch')
  # One full partition backup + the signed candidate + 64 MiB free reserve.
  # The predecessor's fixed 192 MiB estimate needlessly rejects this 17 MiB image.
  if shutil.disk_usage('/').free < SIZE + len(candidate) + 64 * 1024 * 1024:
    raise RuntimeError('Insufficient root filesystem rollback space')
  run(['/usr/bin/systemctl', 'is-enabled', '--quiet', 'comma-modem-usb.service'])
  with writable():
    ROOT.mkdir(mode=0o700)
    (ROOT / 'candidate.img').write_bytes(candidate)
    (ROOT / 'baseline.img').write_bytes(baseline)
    shutil.copyfile(Path(__file__), ROOT / 'kernel_parser_trial.py')
    (ROOT / 'manifest.json').write_text(json.dumps(dict(candidate_sha256=expected,
      partition_sha256=hashlib.sha256(candidate + baseline[len(candidate):]).hexdigest(), kernel_version=kernel_version)))
    (ROOT / 'start_boot_id').write_text(boot_id() + '\n')
    (ROOT / 'phase').write_text('prepared\n')
    for name, data in [(UNIT, SERVICE_TEXT), (TIMER, TIMER_TEXT)]:
      (Path('/etc/systemd/system') / name).write_text(data)
    run(['/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/' + UNIT, '/etc/systemd/system/' + TIMER])
    run(['/usr/bin/systemctl', 'daemon-reload'])
  print('Trial installed; no flash, timer start, or reboot performed.')


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('install', 'flash', 'rollback', 'confirm', 'rearm'))
  parser.add_argument('--source', type=Path)
  parser.add_argument('--sha256')
  parser.add_argument('--kernel-version')
  args = parser.parse_args()
  if os.geteuid() != 0:
    raise RuntimeError('Root required')
  if args.action == 'install':
    if args.source is None or args.sha256 is None or args.kernel_version is None:
      parser.error('install requires --source, --sha256 and --kernel-version')
    install(args.source, args.sha256, args.kernel_version)
  else:
    trusted(ROOT / 'kernel_parser_trial.py')
    fd = os.open('/run/comma-kernel-parser-trial.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      {'flash': flash, 'rollback': rollback, 'confirm': confirm, 'rearm': rearm}[args.action]()
