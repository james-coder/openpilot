"""Optional, read-only thermal exposure summaries. Never a source of driving health."""
import fcntl
import json
import math
import os
import sqlite3
import time
import uuid
from collections import deque
from pathlib import Path

ROOT = Path('/data/thermal-history')
LIMIT = 65536
THRESHOLDS = (50, 55, 60, 65, 70, 75)
SENSORS = ('CPU max', 'GPU max', 'Memory', 'PMIC max', 'Modem max')
TIMELINE_ROWS = 20160  # Seven days at one snapshot per 30 seconds.


class Timeline:
  def __init__(self, root=ROOT):
    self.db = sqlite3.connect(root / 'timeline.sqlite', timeout=.1)
    self.db.execute('PRAGMA journal_mode=DELETE')
    self.db.execute('PRAGMA cache_size=-256')
    page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
    self.db.execute(f'PRAGMA max_page_count={8*1024*1024//page_size}')
    self.db.execute('''CREATE TABLE IF NOT EXISTS samples (slot INTEGER PRIMARY KEY, seq INTEGER, t REAL, session TEXT,
                    mode TEXT, cpu REAL, gpu REAL, memory REAL, pmic REAL, modem REAL, fan REAL, rpm REAL,
                    voltage REAL, watts REAL, screen REAL)''')
    self.seq = self.db.execute('SELECT COALESCE(MAX(seq), -1)+1 FROM samples').fetchone()[0]
    self.session = uuid.uuid4().hex

  def add(self, wall, mode, values, context):
    stamp = wall if math.isfinite(wall) and wall >= 1704067200 else None
    with self.db:
      self.db.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (self.seq % TIMELINE_ROWS, self.seq, stamp, self.session, mode,
         *[values.get(k) if valid_temp(values.get(k)) else None for k in SENSORS],
         *[context.get(k) for k in ('fan_percent', 'fan_rpm', 'voltage', 'watts', 'screen_percent')]))
    self.seq += 1


def valid_temp(value):
  return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and -40 <= value <= 125


def temperatures(ds):
  def maximum(values):
    return max((v for v in values if valid_temp(v)), default=None)
  return dict(zip(SENSORS, (maximum(ds.cpuTempC), maximum(ds.gpuTempC), ds.memoryTempC,
                            maximum(ds.pmicTempC), maximum(ds.modemTempC)), strict=True))


def fresh(sm, service, now):
  return sm.seen[service] and sm.valid[service] and 0 <= now - sm.recv_time[service] < 2


def empty_record():
  return dict(seconds=0., peak=None, avg600=None, avg3600=None,
              above={str(t): 0. for t in THRESHOLDS}, longest={str(t): 0. for t in THRESHOLDS})


def read_summary(root=ROOT):
  with (root / 'summary.json').open('rb') as f:
    raw = f.read(LIMIT + 1)
  if len(raw) > LIMIT:
    raise ValueError('Thermal history exceeds size limit')
  data = json.loads(raw)
  expected = {f'{mode}/{sensor}' for mode in ('offroad', 'onroad') for sensor in SENSORS}
  if data['version'] != 1 or set(data['records']) != expected:
    raise ValueError('Unrecognized thermal history schema')
  for record in data['records'].values():
    if set(record) != set(empty_record()):
      raise ValueError('Malformed thermal record')
    for name in ('above', 'longest'):
      if set(record[name]) != {str(t) for t in THRESHOLDS}:
        raise ValueError('Malformed exposure counters')
    counters = [record['seconds'], *record['above'].values(), *record['longest'].values()]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in counters):
      raise ValueError('Invalid exposure duration')
    for key in ('peak', 'avg600', 'avg3600'):
      if record[key] is not None and not valid_temp(record[key]['value']):
        raise ValueError('Invalid temperature record')
  return data


class History:
  def __init__(self, data=None):
    self.data = data if data is not None else dict(version=1, since=None, updated=None, samples=0, sessions=0, interruptions=0,
      records={f'{mode}/{sensor}': empty_record() for mode in ('offroad', 'onroad') for sensor in SENSORS})
    self.data['sessions'] += 1
    self.previous = {}
    self.windows = {}
    self.streaks = {}

  def break_continuity(self):
    if self.previous:
      self.data['interruptions'] += 1
    self.previous.clear()
    self.windows.clear()
    self.streaks.clear()

  def sample(self, now, wall, mode, values, context):
    # Monotonic time measures exposure. Unsynchronized wall clocks never invent dates.
    stamp = wall if math.isfinite(wall) and wall >= 1704067200 else None
    if self.data['since'] is None and stamp is not None:
      self.data['since'] = stamp
    self.data['updated'] = stamp
    self.data['samples'] += 1
    active = set()
    for sensor in SENSORS:
      value = values.get(sensor)
      if not valid_temp(value):
        continue
      key = f'{mode}/{sensor}'
      active.add(key)
      record = self.data['records'][key]

      def maximum(field, temperature, record=record):
        if record[field] is None or temperature > record[field]['value']:
          record[field] = dict(value=temperature, time=stamp, context=context)

      maximum('peak', value)
      previous = self.previous.get(key)
      window = self.windows.setdefault(key, deque(maxlen=721))
      streak = self.streaks.setdefault(key, {str(t): 0. for t in THRESHOLDS})
      if previous is not None and 0 < now - previous[0] <= 10:
        dt = now - previous[0]
        record['seconds'] += dt
        # Zero-order hold for averages. Threshold durations conservatively require
        # both endpoint samples above threshold; these remain sampled estimates.
        window.append((previous[0], now, previous[1]))
        for threshold in THRESHOLDS:
          t = str(threshold)
          if min(previous[1], value) > threshold:
            record['above'][t] += dt
            streak[t] += dt
            record['longest'][t] = max(record['longest'][t], streak[t])
          else:
            streak[t] = 0.
        while window and window[0][1] <= now - 3600:
          window.popleft()
        for seconds in (600, 3600):
          if window and window[0][0] <= now - seconds:
            integral = sum((end - max(start, now-seconds)) * v for start, end, v in window if end > now-seconds)
            maximum(f'avg{seconds}', integral / seconds)
      else:
        if previous is not None:
          self.data['interruptions'] += 1
        window.clear()
        streak.update(dict.fromkeys(streak, 0.))
      self.previous[key] = now, value
    # Missing sensors and mode transitions must not bridge gaps or retain streaks.
    for key in set(self.previous) - active:
      del self.previous[key]
      self.windows.pop(key, None)
      self.streaks.pop(key, None)

  def save(self, root=ROOT):
    payload = json.dumps(self.data, allow_nan=False, separators=(',', ':'))
    if len(payload.encode()) > LIMIT:
      raise ValueError('Thermal summary exceeds fixed limit')
    # Fixed staging name prevents unbounded orphan files after repeated power loss.
    temporary = root / 'summary.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
      f.write(payload)
      f.flush()
      os.fsync(f.fileno())
    os.replace(temporary, root / 'summary.json')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)


def main():
  # Fail closed if scheduling, storage, lock, or schema setup fails. systemd retries
  # this optional service slowly; it is never registered with driving's manager.
  os.nice(19)
  os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  ROOT.mkdir(exist_ok=True)
  with (ROOT / 'collector.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
      data = read_summary()
    except FileNotFoundError:
      data = None
    history = History(data)
    timeline = Timeline()
    from cereal import messaging
    sm = messaging.SubMaster(['deviceState', 'peripheralState'])
    saved = time.monotonic()
    last_timeline = -30.
    while True:
      sm.update(0)
      now = time.monotonic()
      if fresh(sm, 'deviceState', now):
        ds = sm['deviceState']
        context = dict(fan_percent=int(ds.fanSpeedPercentDesired), screen_percent=float(ds.screenBrightnessPercent),
                       watts=float(ds.powerDrawW))
        if fresh(sm, 'peripheralState', now):
          context.update(fan_rpm=int(sm['peripheralState'].fanSpeedRpm), voltage=float(sm['peripheralState'].voltage) / 1000)
        history.sample(now, time.time(), 'onroad' if ds.started else 'offroad', temperatures(ds), context)  # noqa: TID251
        if now - last_timeline >= 30:
          timeline.add(time.time(), 'onroad' if ds.started else 'offroad', temperatures(ds), context)  # noqa: TID251
          last_timeline = now
      else:
        history.break_continuity()
      if now - saved >= 60:
        history.save()
        saved = now
      time.sleep(5)


if __name__ == '__main__':
  main()
