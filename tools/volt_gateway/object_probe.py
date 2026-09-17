"""One authenticated parked Object-CAN status exchange; no actuation or updates."""
import argparse
import json
from pathlib import Path
import resource
import struct

from openpilot.tools.volt_gateway.device_cli import Device, credentials
from openpilot.tools.volt_gateway.object_transport import boardd_transport


def main():
  resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--pairing', type=Path, required=True)
  args = parser.parse_args()
  keys = credentials(args.pairing)
  transport = boardd_transport()
  device = Device(transport, keys)
  try:
    info = device.command(1)
    if len(info) != 46 or info[:2] != b'\1\1' or info[38:41] != bytes([3, 3, 2]):
      raise ValueError('incompatible Object gateway firmware')
    if device.command(14) != b'\0':
      raise ValueError('unexpected vehicle transmission rule')
    buses = {bus: list(struct.unpack('>14I', device.command(3, bytes([bus])))) for bus in (0, 1, 3)}
    print(json.dumps({'authenticated': True, 'build': info[6:38].hex(), 'bus_status': buses,
                      'host_frames_sent': transport.sent}))
  finally:
    # Unsafe/expired state also blocks this close; gateway session expires itself.
    device.close()


if __name__ == '__main__':
  main()
