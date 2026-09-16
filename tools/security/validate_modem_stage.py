"""Benign offroad candidate validation, no profile changes or modem reset.

Load staged modules without replacing installed files or restarting manager.
Raw SIM identifiers and AT responses are deliberately not printed or saved.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import sys
import time
from struct import pack, unpack_from

from openpilot.system.aranet.safety import offroad


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


@contextmanager
def at_lock():
  fd = os.open('/dev/shm/modem.lock', os.O_RDWR)
  try:
    for attempt in range(10):
      offroad()
      try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except BlockingIOError:
        if attempt == 9:
          raise
        time.sleep(.2)
    yield
  finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def validate(stage, mode):
  offroad()
  helper = load('openpilot.system.hardware.tici.modem_input', stage / 'system/hardware/tici/modem_input.py')
  if mode == 'at':
    import serial
    class Recorder:
      def __init__(self, port):
        self.port = port
        self.data = bytearray()
      @property
      def timeout(self):
        return self.port.timeout
      @timeout.setter
      def timeout(self, value):
        self.port.timeout = value
      def read(self, size):
        data = self.port.read(size)
        self.data.extend(data)
        return data
    for command, prefix in [('AT+CREG?', '+CREG:'), ('AT+CGREG?', '+CGREG:'), ('AT+CSQ', '+CSQ:'),
                            ('AT+QGPS?', '+QGPS:'), ('AT+QCCID', '+QCCID:')]:
      with at_lock(), serial.Serial('/dev/modem_at0', 9600, timeout=5, write_timeout=5) as port:
        port.reset_input_buffer()
        port.write((command + '\r').encode())
        recorder = Recorder(port)
        actual = helper.read_at_response(recorder)
      legacy = []
      for raw in recorder.data.splitlines():
        line = raw.decode(errors='ignore').strip()
        if line == 'OK':
          break
        if line == 'ERROR' or line.startswith('+CME ERROR'):
          raise RuntimeError('Read-only query rejected')
        if line:
          legacy.append(line)
      assert actual == legacy and any(line.startswith(prefix) for line in actual)
      print('PASS read-only AT and legacy parity', command, 'bytes', len(recorder.data), flush=True)
  elif mode == 'diag':
    from cereal import messaging
    sm = messaging.SubMaster(['managerState'])
    for _ in range(20):
      sm.update(100)
      if sm.seen['managerState']:
        break
    assert sm.seen['managerState'] and sm.valid['managerState']
    assert not any(p.name == 'qcomgpsd' and (p.running or p.shouldBeRunning) for p in sm['managerState'].processes)
    module = load('staged_modemdiag', stage / 'system/qcomgpsd/modemdiag.py')
    offroad()
    diag = module.ModemDiag()
    try:
      opcode, data = module.send_recv(diag, module.DIAG_LOG_CONFIG_F, pack('<3xI', module.LOG_CONFIG_RETRIEVE_ID_RANGES_OP))
      assert opcode == module.DIAG_LOG_CONFIG_F and len(data) == 75
      assert unpack_from('<3xII', data) == (1, 0)
      masks = unpack_from('<16I', data, 11)
      assert max(masks) <= 4096
      print('PASS read-only DIAG range response; length', len(data), 'maximum mask bits', max(masks), flush=True)
    finally:
      diag.serial.close()
  elif mode == 'sim':
    module = load('staged_lpa', stage / 'system/hardware/tici/lpa.py')
    class ReadOnlyClient(module.AtClient):
      calls = 0
      def _reset_modem(self):
        raise RuntimeError('Modem resets forbidden in read-only validation')
      def open_isdr(self):
        self._open_isdr_once()  # no reset/retry escalation
      def query(self, command):
        offroad()
        self.calls += 1
        if self.calls > 64 or not command.startswith(('AT+CCHO=', 'AT+CCHC=', 'AT+CGLA=')):
          raise RuntimeError('Read-only session request limit')
        return super().query(command)
    with at_lock():
      client = ReadOnlyClient('/dev/modem_at0', 9600, 5)
      try:
        client.open_isdr()
        profiles = module.list_profiles(client)
        print('PASS read-only SIM profile parsing; count', len(profiles),
              'enabled', sum(p.get('profileState') == 'enabled' for p in profiles), flush=True)
      finally:
        client.close()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('stage', type=Path)
  parser.add_argument('mode', choices=('at', 'diag', 'sim'))
  args = parser.parse_args()
  validate(args.stage, args.mode)
