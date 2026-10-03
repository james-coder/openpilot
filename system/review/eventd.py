#!/usr/bin/env python3
"""Dashcam event recorder (eventd): protects the footage around a hard-braking, ABS or extreme-g event and notes it.

OPTIONAL. Listed in selfdrive/selfdrived/events.py OPTIONAL_PROCESSES: if this process is absent, stopped, crashed,
denied a permission or the review directories are unusable, driving, engagement and disengagement are unchanged. It only
subscribes to carState and accelerometer, sends no CAN, and never touches controls. Its only effects are:
  * `user.preserve` on the current (and, while an episode runs, the next) log segment directory, the same flag the
    flag button sets, so the deleter keeps it;
  * /data/media/0/review/events.jsonl (one line per start/update/end of an episode);
  * /data/review/last_event.json, which the UI badge reads (selfdrive/ui/onroad/event_overlay.py).
Disable without a rebuild: create /data/review/eventd.off.
"""
import json
import os
import re
import signal
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from openpilot.common.swaglog import cloudlog

PRESERVE_ATTR = 'user.preserve'
SEGMENT = re.compile(r'^(.+)--([0-9]+)$')
EVENT_LOG = Path('/data/media/0/review/events.jsonl')
LAST_EVENT = Path('/data/review/last_event.json')
DISABLE_FLAG = Path('/data/review/eventd.off')
PROTECT_AHEAD_S = 30.0   # keep flagging the newest segment this long after the last trigger (episode crosses a boundary)
LOOP_S = 0.02


def boot_id() -> str:
  try:
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  except OSError:
    return ''


def boot_seconds() -> float:
  return time.clock_gettime(time.CLOCK_BOOTTIME)


def current_segment(root: Path) -> str | None:
  """Newest segment directory; among those still being written (a *.lock file inside) when any exist."""
  best, best_locked = None, None
  try:
    with os.scandir(root) as entries:
      names = [(e.name, e.path) for e in entries if e.is_dir(follow_symlinks=False)]
  except OSError:
    return None
  for name, path in names:
    m = SEGMENT.match(name)
    if not m:
      continue
    key = (m.group(1), int(m.group(2)))
    if best is None or key > best[0]:
      best = (key, name)
    try:
      with os.scandir(path) as inner:
        locked = any(n.name.endswith('.lock') for n in inner)
    except OSError:
      locked = False
    if locked and (best_locked is None or key > best_locked[0]):
      best_locked = (key, name)
  chosen = best_locked or best
  return chosen[1] if chosen else None


def protect(root: Path, name: str) -> bool:
  from openpilot.system.loggerd.xattr_cache import setxattr
  try:
    setxattr(str(root / name), PRESERVE_ATTR, b'1')
    return True
  except OSError as e:
    cloudlog.warning(f'eventd: could not protect {name}: {e}')
    return False


def atomic_json(path: Path, value) -> None:
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  fd, tmp = tempfile.mkstemp(prefix='.ev-', dir=path.parent)
  try:
    with os.fdopen(fd, 'w') as f:
      json.dump(value, f, allow_nan=False)
      f.flush()
      os.fsync(f.fileno())
    os.replace(tmp, path)
  finally:
    if os.path.exists(tmp):
      os.unlink(tmp)


def append_line(path: Path, value) -> None:
  path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  with open(path, 'a') as f:
    f.write(json.dumps(value, allow_nan=False) + '\n')
    f.flush()
    os.fsync(f.fileno())


class Recorder:
  """Turns detector events into actions. Every action is individually guarded: one failing never blocks the others."""

  def __init__(self, root: Path, event_log: Path | None = None, last_event: Path | None = None, protect_fn=None):
    # resolved at call time (not as default arguments) so the module constants and `protect` can be replaced in tests
    self.root = Path(root)
    self.event_log = Path(event_log) if event_log else EVENT_LOG
    self.last_event = Path(last_event) if last_event else LAST_EVENT
    self.protect_fn = protect_fn or protect
    self.counter = 0
    self.protected: set[str] = set()
    self.protect_until = -1e9
    self.boot = boot_id()

  def _guard(self, what, fn, *a):
    try:
      return fn(*a)
    except Exception as e:  # an optional feature must never raise into the loop
      cloudlog.warning(f'eventd: {what} failed: {type(e).__name__}: {e}')
      return None

  def ensure_protected(self, now: float) -> None:
    if now > self.protect_until:
      return
    seg = current_segment(self.root)
    if seg and seg not in self.protected and self._guard('protect', self.protect_fn, self.root, seg):
      self.protected.add(seg)

  def handle(self, events: list[dict], now: float) -> None:
    for e in events:
      if e['type'] in ('start', 'update'):
        self.protect_until = now + PROTECT_AHEAD_S
        self.ensure_protected(now)  # first, and before any file I/O that could be slow
      if e['type'] == 'start':
        self.counter += 1
      seg = current_segment(self.root)
      rec = dict(v=1, id=self.counter, phase=e['type'], mono=e['t'], wall=datetime.now(UTC).timestamp(), kinds=e['kinds'],
                 peaks={k: round(v, 3) for k, v in e['peaks'].items()}, segment=seg)
      self._guard('event log', append_line, self.event_log, rec)
      if e['type'] != 'end':  # the banner marks the moment of the event; the closing summary must not re-show it
        shown = dict(rec, boot_id=self.boot, written_boot_s=boot_seconds())
        self._guard('badge file', atomic_json, self.last_event, shown)


def run(stop: threading.Event) -> None:
  from cereal import messaging
  from openpilot.system.hardware.hw import Paths
  from openpilot.system.review.detector import EventDetector
  try:
    os.nice(10)
  except OSError:
    pass
  car = messaging.sub_sock('carState', conflate=False)
  imu = messaging.sub_sock('accelerometer', conflate=False)
  det, rec = EventDetector(), Recorder(Path(Paths.log_root()))
  last_tick = 0.0
  errors = 0
  while not stop.is_set():
    try:
      out = []
      for ev in messaging.drain_sock(car, wait_for_one=False):
        out += det.car_state(ev.logMonoTime / 1e9, float(ev.carState.vEgo), bool(ev.carState.brakePressed))
      for ev in messaging.drain_sock(imu, wait_for_one=False):
        v = list(ev.accelerometer.acceleration.v)
        if len(v) == 3:
          out += det.accel(ev.logMonoTime / 1e9, float(v[0]), float(v[1]), float(v[2]))
      now = boot_seconds()
      if out:
        rec.handle(out, now)
      if now - last_tick >= 1.0:
        last_tick = now
        rec.ensure_protected(now)
        rec.handle(det.tick(now), now)
      errors = 0
    except Exception as e:
      errors += 1
      if errors in (1, 10, 100):
        cloudlog.warning(f'eventd: loop error #{errors}: {type(e).__name__}: {e}')
      stop.wait(min(0.1 * errors, 1.0))  # short: events last seconds, so a blind spell should too
    stop.wait(LOOP_S)


def main(stop: threading.Event | None = None):
  if stop is None:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
  if DISABLE_FLAG.exists():
    cloudlog.info('eventd: disabled by /data/review/eventd.off')
    stop.wait()  # idle instead of exiting, so the manager does not restart-loop
    return
  try:
    run(stop)
  except Exception as e:
    cloudlog.warning(f'eventd: startup failed: {type(e).__name__}: {e}')
    stop.wait(5)


if __name__ == '__main__':
  main()
