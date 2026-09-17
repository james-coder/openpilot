"""Privileged, optional Bluetooth owner. Fixed UART; passive reception only.

The local feed is receive-only. Clients cannot supply commands or device paths.
Initialization always requires live telemetry proving ignition-off, or Park/standstill/
disengaged, including in children. See openpilot.system.aranet.safety.OffroadGate.
"""
import contextlib
import fcntl
import grp
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

from openpilot.selfdrive.car.aranet import SENSOR, advertisements, decode, background_priority
from openpilot.system.aranet.protocol import ASSETS, SOCKET, encode
from openpilot.system.aranet.safety import offroad, UnsafeInitialization

LOG = logging.getLogger('aranet-bluetooth')


def stop(child, sig=signal.SIGTERM):
  if child is not None and child.poll() is None:
    child.send_signal(sig)
    try:
      child.wait(timeout=3)
    except subprocess.TimeoutExpired:
      child.kill()
      child.wait(timeout=3)


def run_checked(argv, timeout=10):
  offroad()
  # Only fixed trusted tools/modules are called; no packet/payload logging.
  try:
    # BlueZ's shell event loop stalls with systemd's /dev/null stdin. An EOF
    # pipe selects working noninteractive behavior (verified with read-only info).
    result = subprocess.run(argv, input=b'', capture_output=True, timeout=timeout)
  except subprocess.TimeoutExpired as exc:
    detail = ((exc.stdout or b'') + (exc.stderr or b'')).decode(errors='replace')[-180:]
    raise RuntimeError(f'{Path(argv[0]).name} timed out: {detail or "no tool output"}') from exc
  if result.returncode:
    detail = result.stderr.decode(errors='replace').strip().splitlines()
    raise RuntimeError(f'{Path(argv[0]).name} exit {result.returncode}: ' + (detail[-1][:180] if detail else 'no error detail'))
  return result.stdout.decode(errors='replace')


class Radio:
  def __init__(self):
    self.attach = None
    self.scanner = None
    self.receiver = None

  def ready(self):
    return self.attach is not None and self.attach.poll() is None and Path('/sys/class/bluetooth/hci0').exists()

  def initialize(self):
    offroad()
    if Path('/sys/class/bluetooth/hci0').exists():
      raise RuntimeError('Bluetooth is owned by another launcher; no reset attempted')
    self.close()
    for path in ('/dev/btpower', '/dev/ttyHS1'):
      if not Path(path).exists():
        raise RuntimeError(f'Required Bluetooth device missing: {path}')
    result = subprocess.run(['fuser', '/dev/ttyHS1'], capture_output=True, timeout=3)
    if result.returncode != 1:
      raise RuntimeError('Bluetooth UART occupied or ownership check failed')
    with open('/dev/btpower', 'r+b', buffering=0) as device:
      offroad()
      fcntl.ioctl(device, 0xbfad, 0)
      time.sleep(2)
      offroad()
      fcntl.ioctl(device, 0xbfad, 1)
    time.sleep(3)
    for entry in Path('/sys/class/rfkill').glob('rfkill*'):
      if (entry / 'type').read_text().strip() == 'bluetooth':
        offroad()
        (entry / 'soft').write_text('0')
    run_checked([sys.executable, '-m', 'openpilot.system.aranet.uart'], 15)
    run_checked([sys.executable, '-m', 'openpilot.system.aranet.firmware'], 90)
    offroad()
    self.attach = subprocess.Popen([str(ASSETS / 'bin/hciattach'), '-n', '-s', '115200', '/dev/ttyHS1', 'any', '115200', 'flow'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
      deadline = time.monotonic() + 10
      while not self.ready():
        offroad()
        if self.attach.poll() is not None or time.monotonic() >= deadline:
          raise RuntimeError('HCI attachment failed')
        time.sleep(.1)
      run_checked([str(ASSETS / 'bin/hciconfig'), 'hci0', 'up'])
      # Sysfs appearance alone is not readiness: hci0 can still be DOWN INIT.
      deadline = time.monotonic() + 30
      while True:
        info = run_checked([str(ASSETS / 'bin/hciconfig'), '-a', 'hci0'])
        flags = next((line.split() for line in info.splitlines() if 'RUNNING' in line), [])
        if 'UP' in flags and 'INIT' not in flags:
          break
        if time.monotonic() >= deadline:
          raise RuntimeError('Bluetooth controller did not finish initialization')
        time.sleep(.5)
      run_checked([str(ASSETS / 'bin/btmgmt'), '-i', '0', 'le', 'on'], 30)
    except BaseException:
      self.close()
      raise

  def start_scan(self):
    if not self.ready():
      raise RuntimeError('Bluetooth controller unavailable')
    self.receiver = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
      self.receiver.bind((0,))
      self.receiver.setsockopt(0, 2, struct.pack('<IIIH', 1 << 4, 0, 1 << (0x3e-32), 0))
      self.receiver.setblocking(False)
      self.scanner = subprocess.Popen([str(ASSETS / 'bin/hcitool'), '-i', 'hci0', 'lescan', '--passive', '--duplicates'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except BaseException:
      self.stop_scan()
      raise

  def stop_scan(self):
    stop(self.scanner, signal.SIGINT)
    self.scanner = None
    if self.receiver is not None:
      self.receiver.close()
      self.receiver = None

  def close(self):
    self.stop_scan()
    stop(self.attach)
    self.attach = None


def main():
  background_priority()
  def shutdown(*args):
    raise SystemExit(0)
  signal.signal(signal.SIGTERM, shutdown)
  log_path = Path('/var/log/aranet-bluetooth')
  handler = RotatingFileHandler(log_path / 'service.log', maxBytes=262144, backupCount=2)
  handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
  LOG.addHandler(handler)
  LOG.setLevel(logging.INFO)
  radio = Radio()
  client = None
  # Service RuntimeDirectory is root-owned; singleton lock precedes socket replacement.
  with (SOCKET.parent / 'owner.lock').open('w') as lock, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    SOCKET.unlink(missing_ok=True)
    server.bind(str(SOCKET))
    os.chown(SOCKET, 0, grp.getgrnam('comma').gr_gid)
    os.chmod(SOCKET, 0o660)
    server.listen(1)
    server.setblocking(False)
    state, detail, retry, last_status = 'waiting_offroad', 'Waiting for safe Bluetooth initialization', 0., 0.
    last_forward = 0.
    try:
      while True:
        now = time.monotonic()
        try:
          incoming, _ = server.accept()
          if client is None:
            client = incoming
            client.setblocking(False)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
            last_status = 0.
          else:
            incoming.close()
        except BlockingIOError:
          pass
        if client is not None:
          try:
            # EOF or any client-supplied input closes the receive-only connection.
            client.recv(1)
            client.close()
            client = None
          except BlockingIOError:
            pass
          except OSError:
            client.close()
            client = None
        if client is None:
          radio.stop_scan()
        elif now >= retry:
          try:
            if not radio.ready():
              state, detail = 'initializing', 'Initializing Bluetooth while offroad or safely parked'
              client.send(encode(dict(type='status', state=state, message=detail)))
              radio.initialize()
            if radio.scanner is None:
              radio.start_scan()
            if radio.scanner.poll() is not None:
              raise RuntimeError(f'Passive scanner exited {radio.scanner.returncode}')
            state, detail = 'listening', 'Listening; waiting for Aranet4 2954E'
            # Bound work per iteration even in a crowded RF environment.
            for _ in range(32):
              try:
                packet = radio.receiver.recv(4096)
              except BlockingIOError:
                break
              for address, raw, rssi in advertisements(packet):
                if address == SENSOR and decode(raw) is not None and time.monotonic() - last_forward >= 1:
                  try:
                    client.send(encode(dict(type='advertisement', address=address, raw=raw.hex(), rssi=rssi, received=time.time())))  # noqa: TID251
                    last_forward = time.monotonic()
                  except BlockingIOError:
                    pass  # Drop telemetry, never block on a slow recorder.
          except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            radio.stop_scan()
            unsafe = isinstance(exc, UnsafeInitialization)
            state = 'waiting_offroad' if unsafe else 'initialization_failed'
            detail = str(exc)[:240]
            LOG.warning('%s: %s', state, detail)
            # Merely waiting on safe-to-init telemetry: recheck soon, both to react promptly
            # once parked and to keep the safety gate's own subscriptions from going stale.
            # A real hardware/transport failure backs off much further.
            retry = time.monotonic() + (2 if unsafe else 30)
        if client is not None and time.monotonic() - last_status >= 5:
          try:
            client.send(encode(dict(type='status', state=state, message=detail)))
            last_status = time.monotonic()
          except BlockingIOError:
            pass
          except OSError:
            client.close()
            client = None
        time.sleep(.1)
    finally:
      radio.close()
      if client is not None:
        client.close()
      with contextlib.suppress(OSError):
        SOCKET.unlink()


if __name__ == '__main__':
  main()
