"""Off-device candidate authorizer. Requires the early-deny kernel patch.

Never run this on the existing permissive production kernel. This program
does not make a late userspace policy race-free; the kernel enforces default
deny and driver pinning independently. Descriptor equality is not identity.
"""
import fcntl
import os
from pathlib import Path
import re
import time

CONTROLLER = Path('/sys/devices/platform/soc/a800000.ssusb/a800000.dwc3/xhci-hcd.0.auto')
EXPECTED = bytes.fromhex('''
12010002ef0201407c2c25011803010200010902d100050100a0fa0904000002ffffff00070581020002000705010200
02000904010003ff00000005240010010524010000042402020524060000070583030a00090705820200020007050202
0002000904020003ff00000005240010010524010000042402020524060000070585030a000907058402000200070503
020002000904030003ff00000005240010010524010000042402020524060000070587030a0009070586020002000705
04020002000904040003ffffff00070589030800090705880200020007050502000200
''')
DRIVERS = ('option', 'option', 'option', 'option', 'qmi_wwan')


def matches(data):
  return data == EXPECTED


def small_read(path, maximum=64):
  with path.open('rb') as f:
    return f.read(maximum + 1)


def authorize_device(bus, device):
  # Fixed direct-child physical port; not modem-provided VID/PID or a bus ID.
  if not re.fullmatch(r'usb[0-9]+', bus.name) or device.parent != bus:
    raise RuntimeError('Wrong physical path')
  if device.name != bus.name[3:] + '-1':
    raise RuntimeError('Unexpected downstream port')
  for name in ('authorized_default', 'interface_authorized_default'):
    if small_read(bus / name).strip() != b'0':
      raise RuntimeError('Early kernel default-deny absent')
  if not matches(small_read(device / 'descriptors', len(EXPECTED))):
    (device / 'authorized').write_text('0')
    return False
  if small_read(device / 'authorized').strip() != b'1':
    (device / 'authorized').write_text('1')
  interfaces = [device / f'{device.name}:1.{i}' for i in range(5)]
  actual = {p.name for p in device.glob(device.name + ':*')}
  if actual != {p.name for p in interfaces}:
    (device / 'authorized').write_text('0')
    return False
  try:
    # Authorize the complete tested compatibility set before any explicit probe.
    for intf in interfaces:
      if small_read(intf / 'authorized').strip() != b'1':
        (intf / 'authorized').write_text('1')
    for intf, expected_driver in zip(interfaces, DRIVERS, strict=True):
      driver = intf / 'driver'
      if not driver.exists():
        Path('/sys/bus/usb/drivers_probe').write_text(intf.name)
      if driver.resolve(strict=True).name != expected_driver:
        raise RuntimeError('Unexpected driver binding')
  except (OSError, RuntimeError):
    (device / 'authorized').write_text('0')
    raise
  return True


def scan():
  for bus in CONTROLLER.iterdir():
    if not re.fullmatch(r'usb[0-9]+', bus.name):
      continue
    device = bus / (bus.name[3:] + '-1')
    if device.exists():
      if not authorize_device(bus, device):
        raise RuntimeError('Modem descriptor/interface profile rejected')


def main():
  if os.geteuid() != 0:
    raise RuntimeError('Root required')
  # This DT marker exists only in the off-device policy-enabled candidate.
  marker = Path('/sys/firmware/devicetree/base/soc/ssusb@a800000/comma,untrusted-modem-host')
  if not marker.exists():
    raise RuntimeError('Refusing kernel without physical-host policy marker')
  fd = os.open('/run/comma-modem-usb.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  with os.fdopen(fd, 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    next_log = 0.0
    while True:
      try:
        scan()
      except (OSError, RuntimeError):
        if time.monotonic() >= next_log:
          print('Modem authorization unavailable/rejected; interfaces remain denied', flush=True)
          next_log = time.monotonic() + 60
      time.sleep(2)


if __name__ == '__main__':
  main()
