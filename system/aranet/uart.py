"""WCN3990-specific power pulse then a read-only controller version request.

Pulse sequence: Linux v5.4 drivers/bluetooth/hci_qca.c qca_wcn3990_init.
Version request: drivers/bluetooth/btqca.h EDL_PATCH_VER_REQ_CMD.
Only the dedicated Bluetooth UART is opened; no CAN access.
"""
import fcntl
import os
import select
import subprocess
import termios
import time

from openpilot.system.aranet.safety import offroad


def configure(fd, speed, flow):
  offroad()
  a = termios.tcgetattr(fd)
  a[0] = a[1] = a[3] = 0
  a[2] = termios.CLOCAL | termios.CREAD | termios.CS8 | (termios.CRTSCTS if flow else 0)
  a[4] = a[5] = speed
  a[6][termios.VMIN] = 0
  a[6][termios.VTIME] = 0
  termios.tcsetattr(fd, termios.TCSANOW, a)


def main():
  offroad()
  if subprocess.run(['fuser', '/dev/ttyHS1'], capture_output=True).returncode == 0:
    raise RuntimeError('Bluetooth UART is in use')
  fd = os.open('/dev/ttyHS1', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
  try:
    configure(fd, termios.B2400, False)
    termios.tcflush(fd, termios.TCIOFLUSH)
    offroad()
    os.write(fd, b'\xc0')
    time.sleep(.1)
    configure(fd, termios.B115200, False)
    os.write(fd, b'\xfc')
    time.sleep(.2)
  finally:
    os.close(fd)
  fd = os.open('/dev/ttyHS1', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
  try:
    configure(fd, termios.B115200, True)
    termios.tcflush(fd, termios.TCIOFLUSH)
    os.write(fd, bytes.fromhex('01 00 fc 01 19'))
    received = b''
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
      offroad()
      if select.select([fd], [], [], .2)[0]:
        received += os.read(fd, 4096)
        if len(received) > 4096:
          break
    print('Controller version response:', received.hex() or 'NO RESPONSE', flush=True)
    if not received.startswith(bytes.fromhex('04 ff 0e 00 02')):
      raise RuntimeError('No valid Qualcomm controller-version response; refusing to continue')
    modem = fcntl.ioctl(fd, termios.TIOCMGET, bytes(4))
    print('UART modem status:', int.from_bytes(modem, 'little'))
  finally:
    os.close(fd)


if __name__ == '__main__':
  main()
