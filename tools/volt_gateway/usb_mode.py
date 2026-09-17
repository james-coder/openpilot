"""Explicit non-programming Panda mode transitions. USB-only bench operation.

Never imports Panda, unlocks flash, sends bulk writes, or changes option bytes.
One attempt only; loss of enumeration must be diagnosed, not reset repeatedly.
"""

import argparse
from contextlib import closing
import json

import usb1


SERIAL = '370022000651363038363036'


def transition(action):
  actions = {'softloader': (0xddcc, 0xd1, 1), 'rom': (0xddee, 0xd1, 0), 'application': (0xddee, 0xd8, 0)}
  pid, request, value = actions[action]
  with usb1.USBContext() as context:
    matches = []
    for device in context.getDeviceList(skip_on_error=False):
      if (device.getVendorID(), device.getProductID()) == (0xbbaa, pid):
        with closing(device.open()) as handle:
          if handle.getASCIIStringDescriptor(device.getSerialNumberDescriptor()).lower() == SERIAL:
            matches.append(device)
    if len(matches) != 1:
      raise RuntimeError('Exact source-mode device not present')
    with closing(matches[0].open()) as handle:
      version = bytes(handle.controlRead(0xc0, 0xd6, 0, 0, 64, timeout=2000)).rstrip(b'\0')
      if version != b'v1.7.3-EON-unknown-RELEASE':
        raise RuntimeError('Unreviewed source firmware version')
      if pid == 0xddee:
        echo = bytes(handle.controlRead(0xc0, 0xb0, 0, 0, 12, timeout=2000))
        if len(echo) != 12 or echo[2:8] != b'\xb0\x4f\xde\xad\xd0\x0d':
          raise RuntimeError('Unexpected softloader signature')
      result = 'request completed; target enumeration not yet verified'
      try:
        handle.controlWrite(0x40, request, value, 0, b'', timeout=2000)
      except (usb1.USBErrorNoDevice, usb1.USBErrorIO, usb1.USBErrorTimeout):
        result = 'disconnected during reset; target enumeration must be checked; not retried'
  return {'serial': SERIAL, 'action': action, 'request': hex(request), 'value': value, 'result': result,
          'flash_write': False}


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('softloader', 'rom', 'application'))
  print(json.dumps(transition(parser.parse_args().action), indent=2))
