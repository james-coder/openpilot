"""Load device-supplied WCN3990 ROM 2.1 patch/config into volatile controller RAM.

Protocol: Linux btqca.c/btqca.h (v5.4 plus modern type-4 NVM parsing).
Only UART transport speed/IBS/deep-sleep settings are adapted for plain H4.
No flash partition writes. Dedicated Bluetooth UART must already be powered/initialized.
"""
import fcntl
import hashlib
import os
import contextlib
import select
import struct
import subprocess
import termios
import time

from openpilot.system.aranet.safety import offroad
from openpilot.system.aranet.protocol import ASSETS as ROOT
from openpilot.system.aranet.uart import configure

HASHES = {
  'crbtfw21.tlv': '2a42859081393ca6a50a177773385eac4fcabb9f21fc1b78299ed35bf8a598c4',
  'crnv21.bin': '18cc38fff25a6a56bc163f1874184da93d90882dc047c9f0c534ed9888c543b3',
}


def validate(data, kind):
  if len(data) < 4 or data[0] != kind or int.from_bytes(data[1:4], 'little') != len(data)-4:
    raise ValueError('Invalid firmware TLV length/type')


def prepare_nvm(original):
  validate(original, 4)
  data = bytearray(original)
  if len(data) < 8 or data[4] != 2:
    raise ValueError('Expected Bluetooth section first')
  end = 8 + int.from_bytes(data[5:8], 'little')
  if end > len(data):
    raise ValueError('Truncated NVM section')
  i, changed = 8, []
  while i < end:
    if i+12 > end:
      raise ValueError('Truncated NVM tag header')
    tag, size = struct.unpack_from('<HH', data, i)
    start = i+12
    if start+size > end:
      raise ValueError('Truncated NVM tag')
    if tag == 17:
      if size < 3:
        raise ValueError('Invalid HCI tag')
      data[start] &= ~0x80  # H4 has no Qualcomm IBS handshake.
      data[start+2] = 0     # Qualcomm baud enum: 115200.
      changed.append(tag)
    elif tag == 27:
      if size < 1:
        raise ValueError('Invalid sleep tag')
      data[start] &= ~1     # Keep controller awake without IBS.
      changed.append(tag)
    i = start+size
  if changed != [17, 27]:
    raise ValueError('Unexpected transport/sleep tags')
  # Preserve every byte outside these three documented transport fields.
  return bytes(data)


class UART:
  def __init__(self, fd, log):
    self.fd, self.log, self.buffer = fd, log, bytearray()

  def record(self, direction, raw):
    pass  # No firmware payload dumps in production.

  def send(self, opcode, payload=b''):
    offroad()
    packet = b'\x01' + struct.pack('<HB', opcode, len(payload)) + payload
    self.record('tx', packet)
    view = memoryview(packet)
    deadline = time.monotonic()+5
    while view:
      offroad()
      if time.monotonic() > deadline:
        raise TimeoutError('UART write timeout')
      if select.select([], [self.fd], [], .1)[1]:
        try:
          view = view[os.write(self.fd, view):]
        except BlockingIOError:
          pass
    # Nonblocking UART writes are bounded above; avoid unbounded tcdrain.

  def event(self, timeout=5):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
      offroad()
      # ROM can emit Qualcomm in-band wake bytes between H4 events before
      # the no-IBS configuration takes effect. These are not HCI events.
      if self.buffer and self.buffer[0] in (0xfd, 0xfe, 0xfc):
        wake = bytes(self.buffer[:1])
        del self.buffer[:1]
        self.record('ibs_rx', wake)
        if wake == b'\xfd':
          os.write(self.fd, b'\xfc')
          self.record('ibs_tx', b'\xfc')
        continue
      if len(self.buffer) >= 3:
        if self.buffer[0] != 4:
          raise RuntimeError('Unexpected UART packet: '+self.buffer.hex())
        size = self.buffer[2]+3
        if len(self.buffer) >= size:
          raw = bytes(self.buffer[:size])
          del self.buffer[:size]
          self.record('rx', raw)
          if raw[1] == 0x10:
            raise RuntimeError('Controller hardware error: '+raw.hex())
          return raw
      if select.select([self.fd], [], [], .1)[0]:
        self.buffer.extend(os.read(self.fd, 4096))
    raise TimeoutError('UART event timeout; buffered='+self.buffer.hex())

  def version(self):
    self.send(0xfc00, b'\x19')
    version = None
    for _ in range(8):
      event = self.event()
      if event.startswith(bytes.fromhex('04ff0e0002')):
        version = struct.unpack('<IHHI', event[5:])
      elif event[1] == 0x0e:
        if event[-1] != 0:
          raise RuntimeError('Version command failed')
        if version is not None:
          return version
    raise RuntimeError('Missing controller version')

  def download(self, data, mode):
    for start in range(0, len(data), 243):
      chunk = data[start:start+243]
      self.send(0xfc00, bytes([0x1e, len(chunk)]) + chunk)
      if mode == 0 or start+len(chunk) == len(data):
        event = self.event()
        if event != bytes.fromhex('04ff03000400'):
          raise RuntimeError('TLV acknowledgement failed: '+event.hex())
        if mode == 0:
          event = self.event()
          if event[1] != 0x0e or event[-1] != 0:
            raise RuntimeError('TLV command-complete failed: '+event.hex())
    print('Loaded TLV type', data[0], 'bytes', len(data), flush=True)


def main():
  offroad()
  firmware = {}
  for name, expected in HASHES.items():
    data = (ROOT/'firmware'/name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
      raise ValueError('Firmware hash mismatch: '+name)
    firmware[name] = data
  patch = firmware['crbtfw21.tlv']
  validate(patch, 1)
  header = struct.unpack('<IIBBBBHHHHI', patch[4:28])
  if header[4] != 3 or header[6:9] != (10, 0x0201, 2):
    raise ValueError('Unexpected patch product/ROM/version/download mode')
  nvm = prepare_nvm(firmware['crnv21.bin'])
  if subprocess.run(['fuser', '/dev/ttyHS1'], capture_output=True).returncode == 0:
    raise RuntimeError('Dedicated Bluetooth UART is in use')
  os.umask(0o077)
  fd = os.open('/dev/ttyHS1', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
  try:
    fcntl.ioctl(fd, termios.TIOCEXCL)
    configure(fd, termios.B115200, True)
    with contextlib.nullcontext(None) as log:
      uart = UART(fd, log)
      version = uart.version()
      print('Before firmware:', version, flush=True)
      if version != (10, 1, 0x0201, 0x40010214):
        raise RuntimeError('Unexpected controller version; power-cycle to known ROM first')
      uart.download(patch, 3)
      time.sleep(.1)
      uart.download(nvm, 0)
      uart.send(0x0c03)
      if uart.event() != bytes.fromhex('040e0401030c00'):
        raise RuntimeError('Controller reset failed')
      after = uart.version()
      print('After firmware:', after, flush=True)
      if after[0:3] != (10, 2, 0x0201):
        raise RuntimeError('Patch version not confirmed')
  finally:
    fcntl.ioctl(fd, termios.TIOCNXCL)
    os.close(fd)


if __name__ == '__main__':
  main()
