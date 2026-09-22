#!/usr/bin/env python3
"""modemlogd: persistent record of cellular connectivity.

The modem daemon publishes its full state (registration, signal, operator, band, pppd
connected flag) only to /dev/shm/modem, which is RAM and lost at reboot, and the
manager's stdout scrolls away. deviceState in the rlogs has networkType at 2 Hz but not
the why. This samples the state file every SAMPLE_S and appends a line to
/data/log/modem_events.log whenever anything that matters changes, plus a heartbeat every
HEARTBEAT_S so long steady stretches are still measurable. Read-only, no modem commands.

Line format (one JSON object per line): {"t": unix, "up": seconds_since_boot, ...fields}
Summarize with tools/connectivity_report.py --modem-log.
"""
import json
import os
import time
from pathlib import Path

STATE_PATH = '/dev/shm/modem'
LOG_PATH = Path('/data/log/modem_events.log')
MAX_BYTES = 4 * 1024 * 1024   # rotate: keep one previous file
SAMPLE_S = 5.
HEARTBEAT_S = 300.
FIELDS = ('state', 'connected', 'registration', 'network_type', 'operator', 'band', 'signal_strength', 'signal_quality', 'ip_address')
SIGNAL_STEP = 10  # log a signal change only when it moves at least this much


def read_state(path=STATE_PATH):
  try:
    with open(path) as f:
      s = json.load(f)
    return {k: s.get(k) for k in FIELDS} | {'up': s.get('seconds_since_boot')}
  except (OSError, ValueError, TypeError):
    return None


def changed(prev, cur):
  """True when a field other than a small signal wobble differs."""
  if prev is None or cur is None:
    return prev is not cur
  for k in FIELDS:
    if k in ('signal_strength', 'signal_quality'):
      a, b = prev.get(k) or 0, cur.get(k) or 0
      if abs(a - b) >= SIGNAL_STEP:
        return True
    elif prev.get(k) != cur.get(k):
      return True
  return False


def append(line, path=LOG_PATH):
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_BYTES:
      path.replace(path.with_suffix('.log.1'))
    with path.open('a') as f:
      f.write(json.dumps(line, separators=(',', ':')) + '\n')
  except OSError:
    pass


def main():
  prev = None
  last_write = 0.
  append({'t': time.time(), 'event': 'modemlogd start'})  # noqa: TID251 -- persisted wall timestamps
  while True:
    cur = read_state()
    now = time.time()  # noqa: TID251
    if changed(prev, cur) or now - last_write >= HEARTBEAT_S:
      append({'t': now, **(cur or {'state': 'no-state-file'})})
      prev, last_write = cur, now
    time.sleep(SAMPLE_S)


if __name__ == '__main__':
  main()
