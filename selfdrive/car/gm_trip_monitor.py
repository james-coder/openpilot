"""Preserve existing full route logs by hard link; no CAN socket or transmitter.

Runs at low priority alongside loggerd, not in card/UI/control loops. All buses
and unknown frames remain in the original rlog byte stream. No video is pinned.
"""
import json
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import signal
import tempfile
import threading

TRIP_ROOT = Path('/data/media/0/diagnostics/gm/trips')
MAX_PIN_BYTES = 2 * 1024**3
MIN_FREE_BYTES = 6 * 1024**3
SEGMENT = re.compile(r'(.+)--([0-9]+)$')


def atomic_json(path, value):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  fd, temporary = tempfile.mkstemp(prefix='.trip-', dir=path.parent)
  try:
    with os.fdopen(fd, 'w') as stream:
      json.dump(value, stream, allow_nan=False)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


class RoutePreserver:
  def __init__(self, log_root, destination=TRIP_ROOT, since=None):
    self.log_root, self.destination = Path(log_root), Path(destination)
    self.since = datetime.now(UTC).timestamp() - 120 if since is None else since
    self.status = {'version': 1, 'profile': 'gm_trip_preservation', 'state': 'waiting', 'segments': 0,
                   'message': 'Waiting for full route logs. No CAN requests are sent.'}

  def update(self, *, free_bytes=None):
    self.destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    pinned = list(self.destination.glob('*--*/rlog.zst'))
    used = sum(p.stat().st_size for p in pinned)
    free = free_bytes if free_bytes is not None else os.statvfs(self.destination).f_bavail * os.statvfs(self.destination).f_frsize
    self.status.update(bytes=used, segments=len(pinned), updated_utc=datetime.now(UTC).timestamp())
    if used >= MAX_PIN_BYTES or free < MIN_FREE_BYTES:
      self.status.update(state='limited', message='Preservation budget/free-space limit reached; existing pins retained. Normal route logging is unchanged.')
    else:
      self.status.update(state='waiting', message='Waiting for current full route logs.')
      for folder in sorted(self.log_root.iterdir()):
        source = folder / 'rlog.zst'
        if not SEGMENT.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
          continue
        try:
          info = source.stat()
          if source.is_symlink() or info.st_mtime < self.since:
            continue
          target = self.destination / folder.name / 'rlog.zst'
          if target.exists():
            if not os.path.samefile(source, target):
              raise ValueError('Pinned route name collision')
            continue
          if used + info.st_size > MAX_PIN_BYTES:
            self.status.update(state='limited', message='Preservation budget reached; some segments not pinned.')
            break
          target.parent.mkdir(mode=0o700, exist_ok=True)
          os.link(source, target)  # Same inode, including bytes loggerd appends while the segment is open.
          used += info.st_size
          atomic_json(target.parent / 'source.json', {'source': str(source), 'pinned_utc': datetime.now(UTC).timestamp(),
                                                    'device': info.st_dev, 'inode': info.st_ino})
        except FileNotFoundError:
          continue  # Rotation/deletion race is not evidence of a complete capture.
      if self.status['state'] != 'limited':
        self.status.update(state='preserving', message='Full route logs pinned without copying raw CAN. Coverage/drop checks are in the extracted report.')
    self.status['segments'] = len(list(self.destination.glob('*--*/rlog.zst')))
    if not self.status['segments'] and self.status['state'] == 'preserving':
      self.status.update(state='waiting', message='No current full route logs have been pinned yet.')
    self.status['bytes'] = used
    atomic_json(self.destination / 'status.json', self.status)
    return self.status


def main():
  from openpilot.system.hardware.hw import Paths
  os.nice(10)
  stop = threading.Event()
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  preserver = RoutePreserver(Paths.log_root())
  while not stop.is_set():
    try:
      preserver.update()
    except (OSError, ValueError) as error:
      preserver.status.update(state='error', message=f'Preservation failed: {error}; normal logging unchanged.')
      try:
        atomic_json(TRIP_ROOT / 'status.json', preserver.status)
      except OSError:
        pass
    stop.wait(10)


if __name__ == '__main__':
  main()
