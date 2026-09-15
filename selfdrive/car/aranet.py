"""Selected Aranet4 passive BLE history. No CAN access or Bluetooth connections.

Advertisement layout: Anrijs/Aranet4-Python, client.py, revision
414079ccb3abc6baf95ce32a919a6be3226d6c39. Storage is local, outside route uploads.
"""
import contextlib
import json
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
    self.db.execute('PRAGMA journal_mode=DELETE')
    self.db.execute('PRAGMA max_page_count=1024')  # Default 4096-byte pages: 4 MiB.
    self.db.execute('''CREATE TABLE IF NOT EXISTS readings
                       (slot INTEGER PRIMARY KEY, t REAL, co2 REAL, temp REAL, pressure REAL,
                        humidity REAL, battery INTEGER, interval INTEGER, rssi INTEGER)''')
    row = self.db.execute('SELECT slot,t FROM readings ORDER BY t DESC LIMIT 1').fetchone()
    self.slot, self.last = row if row else (-1, 0)

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
    return db.execute('SELECT t,co2,temp,humidity,interval,battery,rssi FROM readings WHERE t >= ? ORDER BY t LIMIT ?',
                      (time.time() - seconds, MAX_ROWS)).fetchall()  # noqa: TID251 -- persisted wall timestamps


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


def status(message):
  tmp = ROOT / 'status.tmp'
  tmp.write_text(json.dumps({'time': time.time(), 'message': message}))  # noqa: TID251 -- cross-process persisted status
  tmp.replace(ROOT / 'status.json')


def background_priority():
  """Fail closed before scanning if the requested Linux background policy cannot be set.

  Applied in-process so manager and systemd launches behave alike; children inherit it.
  """
  os.setpriority(os.PRIO_PROCESS, 0, 19)
  os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  subprocess.run(['/usr/bin/ionice', '-c', '3', '-p', str(os.getpid())], check=True, timeout=5)


def main():
  import fcntl
  background_priority()
  def shutdown(signum, frame):
    raise SystemExit(0)
  signal.signal(signal.SIGTERM, shutdown)
  os.umask(0o022)
  ROOT.mkdir(exist_ok=True)
  lock = (ROOT / 'collector.lock').open('w')
  fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
  history = History()
  while True:
    scanner = None
    try:
      if (ROOT / 'paused').exists():
        status('Logging paused')
        time.sleep(5)
        continue
      if not Path('/sys/class/bluetooth/hci0').exists():
        status('Bluetooth unavailable; waiting for ignition-off initialization')
        # Existing validated helper refuses bring-up unless fresh telemetry confirms ignition off.
        subprocess.run(['/usr/local/venv/bin/python', '/data/bluetooth-test/probe.py', '--bring-up'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
      with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as sock:
        sock.bind((0,))
        sock.setsockopt(0, 2, struct.pack('<IIIH', 1 << 4, 0, 1 << (0x3e-32), 0))
        sock.settimeout(2)
        scanner = subprocess.Popen(['/data/bluetooth-test/usr/bin/hcitool', '-i', 'hci0', 'lescan', '--passive', '--duplicates'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status('Listening for Aranet4 2954E')
        last_status = time.monotonic()
        while not (ROOT / 'paused').exists():
          if scanner.poll() is not None:
            raise RuntimeError('BLE scanner stopped')
          try:
            packet = sock.recv(4096)
          except TimeoutError:
            packet = b''
          for address, raw, rssi in advertisements(packet):
            if address == SENSOR:
              values = decode(raw)
              if values:
                history.add(values, rssi)
          if time.monotonic() - last_status >= 30:
            status('Listening for Aranet4 2954E')
            last_status = time.monotonic()
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
      status(f'Collector unavailable: {type(exc).__name__}; retrying')
      time.sleep(15)
    finally:
      if scanner is not None and scanner.poll() is None:
        scanner.send_signal(signal.SIGINT)
        try:
          scanner.wait(timeout=3)
        except subprocess.TimeoutExpired:
          scanner.kill()
          scanner.wait()


if __name__ == '__main__':
  main()
