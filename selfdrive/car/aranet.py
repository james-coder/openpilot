"""Selected Aranet4 passive BLE history. No CAN access or Bluetooth connections.

Advertisement layout: Anrijs/Aranet4-Python, client.py, revision
414079ccb3abc6baf95ce32a919a6be3226d6c39. Storage is local, outside route uploads.
"""
import contextlib
import json
import math
import os
from pathlib import Path
import socket
import signal
import sqlite3
import struct
import subprocess
import time

ROOT = Path('/data/aranet')
SENSOR = 'CE:24:29:74:F2:C2'  # Owner's previously identified Aranet4 2954E; never auto-select another.
MAX_ROWS = 21600
RETENTION = 30 * 86400


def decode(raw):
  if len(raw) != 22 or not raw[0] & 0x20:
    return None
  co2, temp, pressure, humidity, battery, status, interval, age = struct.unpack('<xxxxxxxxxHHHBBBHH', (b'\0' + raw)[:22])
  if co2 & 0x8000 or temp & 0x4000 or pressure & 0x8000 or humidity > 100 or battery > 100:
    return None
  if not 1 <= interval <= 3600 or age > max(180, interval * 2):
    return None
  return (co2, temp * .05, pressure * .1, humidity, battery, interval, age)


def advertisements(packet):
  """Decode each report in a legacy LE Advertising Report HCI event, with bounds checks."""
  if len(packet) < 5 or packet[:2] != b'\x04\x3e' or packet[3] != 2 or len(packet) != packet[2] + 3:
    return
  offset = 5
  for _ in range(packet[4]):
    if offset + 9 > len(packet):
      return
    address = ':'.join(f'{v:02X}' for v in packet[offset+2:offset+8][::-1])
    size = packet[offset+8]
    end = offset + 9 + size
    if end >= len(packet):
      return
    data = packet[offset+9:end]
    rssi = struct.unpack('b', packet[end:end+1])[0]
    pos = 0
    while pos < len(data):
      length = data[pos]
      if not length or pos + 1 + length > len(data):
        break
      field = data[pos+1:pos+1+length]
      if field[:3] == b'\xff\x02\x07':
        yield address, field[3:], rssi
      pos += length + 1
    offset = end + 1


class History:
  def __init__(self, root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    self.db = sqlite3.connect(root / 'history.sqlite', timeout=1)
    try:
      self.db.execute('PRAGMA journal_mode=DELETE')
      page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
      self.db.execute(f'PRAGMA max_page_count={4 * 1024 * 1024 // page_size}')
      self.db.execute('''CREATE TABLE IF NOT EXISTS readings
                         (slot INTEGER PRIMARY KEY, t REAL, co2 REAL, temp REAL, pressure REAL,
                          humidity REAL, battery INTEGER, interval INTEGER, rssi INTEGER)''')
      row = self.db.execute('SELECT slot,t FROM readings ORDER BY t DESC LIMIT 1').fetchone()
      self.slot, self.last = row if row else (-1, 0)
    except BaseException:
      self.db.close()
      raise

  def add(self, values, rssi, now=None):
    now = time.time() if now is None else now  # noqa: TID251 -- history must survive reboots
    co2, temp, pressure, humidity, battery, interval, age = values
    measured = now - age
    # Age varies slightly between repeated advertisements; don't treat that jitter as new data.
    if measured - self.last < max(55, interval * .5):
      return False
    slot = (self.slot + 1) % MAX_ROWS
    with self.db:
      self.db.execute('DELETE FROM readings WHERE t < ?', (now - RETENTION,))
      self.db.execute('INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?,?,?,?)',
                      (slot, measured, co2, temp, pressure, humidity, battery, interval, rssi))
    self.slot, self.last = slot, measured
    return True


def read_history(root=ROOT, seconds=86400):
  if not (root / 'history.sqlite').exists():
    return []
  with contextlib.closing(sqlite3.connect(f'file:{root}/history.sqlite?mode=ro', uri=True, timeout=.05)) as db:
    now = time.time()  # noqa: TID251 -- persisted wall timestamps
    rows = db.execute('SELECT t,co2,temp,humidity,interval,battery,rssi FROM readings WHERE t >= ? AND t <= ? ORDER BY t LIMIT ?',
                      (now - seconds, now, MAX_ROWS)).fetchall()
    # Do not feed malformed/NULL database values or future-clock samples into UI drawing.
    return [row for row in rows if all(type(x) in (int, float) and math.isfinite(x) for x in row)
            and 0 <= row[1] <= 32767 and 0 <= row[2] <= 1638.35 and 0 <= row[3] <= 100
            and 1 <= row[4] <= 3600 and 0 <= row[5] <= 100 and -127 <= row[6] <= 0]


def plot_samples(rows, column, buckets=300):
  """Bound drawing cost; preserve extrema and don't draw across omitted outages."""
  stride = max(1, (len(rows) + buckets - 1) // buckets)
  result = []
  for start in range(0, len(rows), stride):
    group = rows[start:start+stride]
    if any(b[0]-a[0] > max(180, 2*max(a[4], b[4])) for a, b in zip(group, group[1:], strict=False)):
      # Conservative: an outage inside this bucket breaks its plotted line.
      result.extend([group[0], None, group[-1]])
    else:
      indexes = sorted({0, len(group)-1, min(range(len(group)), key=lambda i: group[i][column]),
                        max(range(len(group)), key=lambda i: group[i][column])})
      result.extend(group[i] for i in indexes)
  return result


def status(message, state='unavailable', last_advertisement=None, last_write=None):
  payload = {'time': time.time(), 'message': message[:240], 'state': state,  # noqa: TID251 -- persisted wall timestamps
             'last_advertisement': last_advertisement, 'last_write': last_write}
  try:
    tmp = ROOT / 'status.tmp'
    tmp.write_text(json.dumps(payload))
    tmp.replace(ROOT / 'status.json')
  except OSError:
    # Status storage can fail too. Never turn that into a tight exception loop.
    pass


def background_priority():
  """Fail closed before scanning if Linux background policy cannot be set."""
  os.setpriority(os.PRIO_PROCESS, 0, 19)
  os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  subprocess.run(['/usr/bin/ionice', '-c', '3', '-p', str(os.getpid())], check=True, timeout=5)


def consume_advertisement(message, history):
  """Validate the local feed independently of its sender; return reception/write times."""
  from openpilot.system.aranet.protocol import MAX_PACKET
  if message.get('address') != SENSOR:
    raise ValueError('Unexpected sensor')
  raw = message.get('raw')
  if not isinstance(raw, str) or len(raw) != 44 or len(raw) > MAX_PACKET:
    raise ValueError('Invalid advertisement')
  values = decode(bytes.fromhex(raw))
  rssi, received = message.get('rssi'), message.get('received')
  if values is None or type(rssi) is not int or not -127 <= rssi <= 0:
    raise ValueError('Invalid measurement or RSSI')
  if type(received) not in (int, float) or not math.isfinite(received) or abs(time.time() - received) > 30:  # noqa: TID251
    raise ValueError('Invalid reception timestamp')
  wrote = history.add(values, rssi, now=received)
  return received, received if wrote else None


def main():
  import fcntl
  from openpilot.system.aranet.protocol import SOCKET, MAX_PACKET, decode_message
  background_priority()
  def shutdown(signum, frame):
    raise SystemExit(0)
  signal.signal(signal.SIGTERM, shutdown)
  os.umask(0o022)
  # A duplicate launcher exits rather than competing for the database.
  ROOT.mkdir(exist_ok=True)
  with (ROOT / 'collector.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    last_advertisement = last_write = None
    while True:
      history = None
      try:
        if (ROOT / 'paused').exists():
          status('Logging paused', 'paused', last_advertisement, last_write)
          time.sleep(5)
          continue
        history = History(ROOT)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as feed:
          feed.settimeout(2)
          feed.connect(str(SOCKET))
          status('Connected; waiting for Bluetooth status', 'waiting_helper', last_advertisement, last_write)
          last_status = 0.
          last_helper_state = None
          last_message = time.monotonic()
          message_timeout = 15
          while not (ROOT / 'paused').exists():
            try:
              raw = feed.recv(MAX_PACKET + 1)
            except TimeoutError:
              if time.monotonic() - last_message > message_timeout:
                raise RuntimeError('Bluetooth helper stopped responding') from None
              continue
            msg = decode_message(raw)
            last_message = time.monotonic()
            message_timeout = 210 if msg.get('state') == 'initializing' else 15
            if msg['type'] == 'advertisement':
              last_advertisement, written = consume_advertisement(msg, history)
              if written is not None:
                last_write = written
            helper_changed = msg['type'] == 'status' and msg.get('state') != last_helper_state
            if time.monotonic() - last_status >= 5 or helper_changed:
              state, detail = msg.get('state', 'listening'), msg.get('message', 'Listening for Aranet4 2954E')
              if not isinstance(state, str) or not isinstance(detail, str):
                raise ValueError('Invalid helper status')
              if state == 'listening' and last_write is not None:
                if time.time() - last_write <= 240:  # noqa: TID251 -- persisted sample freshness
                  state, detail = 'recording', 'Recording Aranet4 2954E'
                else:
                  state, detail = 'sensor_stale', 'Sensor stale; no fresh readings'
              status(detail, state, last_advertisement, last_write)
              last_status = time.monotonic()
            if msg['type'] == 'status':
              last_helper_state = msg.get('state')
      except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        state = 'storage_failed' if isinstance(exc, sqlite3.Error) else 'unavailable'
        status(f'{type(exc).__name__}: {exc}', state, last_advertisement, last_write)
        time.sleep(30)
      finally:
        if history is not None:
          history.db.close()


if __name__ == '__main__':
  main()
