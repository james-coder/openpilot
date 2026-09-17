"""Read-only direct libusb inspection; never constructs Panda or resets hardware."""

import argparse
from contextlib import closing
import json
import re
import struct


HEALTH_FIELDS = ('uptime', 'voltage_mv', 'current_raw', 'can_rx_errs', 'can_send_errs', 'can_fwd_errs',
                 'gmlan_send_errs', 'faults', 'ignition_line', 'ignition_can', 'controls_allowed',
                 'gas_interceptor_detected', 'car_harness_status', 'usb_power_mode', 'safety_mode',
                 'fault_status', 'power_save_enabled')


def decode_legacy_health(raw):
  if len(raw) != 41:
    raise ValueError('unrecognized historical health layout; do not guess')
  result = dict(zip(HEALTH_FIELDS, struct.unpack('<8I9B', raw), strict=True))
  for name in ('ignition_line', 'ignition_can', 'controls_allowed', 'gas_interceptor_detected', 'power_save_enabled'):
    if result[name] not in (0, 1):
      raise ValueError('invalid historical health boolean')
  return result


def inspect(serial: str, *, health=False) -> dict:
  import usb1

  if not re.fullmatch(r'[0-9A-Fa-f]{24}', serial):
    raise ValueError('exact 24-character Panda serial required')
  with usb1.USBContext() as context:
    matches = []
    for device in context.getDeviceList(skip_on_error=False):
      if (device.getVendorID(), device.getProductID()) != (0xbbaa, 0xddcc):
        continue
      with closing(device.open()) as handle:
        observed = handle.getASCIIStringDescriptor(device.getSerialNumberDescriptor())
      if observed.lower() == serial.lower():
        matches.append(device)
    if len(matches) != 1:
      raise RuntimeError('expected exactly one matching Panda application')
    device = matches[0]
    with closing(device.open()) as handle:
      # Version-matched IN-only queries. No configuration/interface claim needed.
      version = bytes(handle.controlRead(0xc0, 0xd6, 0, 0, 64, timeout=2000))
      kind = bytes(handle.controlRead(0xc0, 0xc1, 0, 0, 1, timeout=2000))
      packet = None
      if health:
        if version.rstrip(b'\0') != b'v1.7.3-EON-unknown-RELEASE' or kind != b'\x01':
          raise ValueError('health decoder only reviewed for this historical White firmware')
        packet = decode_legacy_health(bytes(handle.controlRead(0xc0, 0xd2, 0, 0, 64, timeout=2000)))
    result = {'serial': serial, 'vid': 'bbaa', 'pid': 'ddcc', 'bus': device.getBusNumber(),
            'address': device.getDeviceAddress(), 'version': version.rstrip(b'\0').decode('ascii', errors='replace'),
            'hardware_type_hex': kind.hex(), 'usb_owner': 'WSL', 'device_changed': False}
    if packet is not None:
      result['health'] = packet
      result['health_limitations'] = 'Firmware-reported values; not proof of physical disconnection, silent pins or mux routing'
    return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--serial', required=True)
  parser.add_argument('--health', action='store_true', help='additional read-only historical health query')
  args = parser.parse_args()
  print(json.dumps(inspect(args.serial, health=args.health), indent=2))


if __name__ == '__main__':
  main()
