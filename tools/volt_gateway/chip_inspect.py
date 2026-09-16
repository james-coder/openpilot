"""Exact-target ROM DFU register inspection. No flash/option-byte programming.

Only reviewed read addresses; SET_ADDRESS is a DFU pointer command, not a memory
write. No unprotect, erase, arbitrary addresses, mode entry or firmware payload.
ROM may reject peripheral/system reads; report that rather than bypassing it.
"""
from contextlib import closing
import json
import time

SERIAL = '365236793036'
FIELDS = {'flash_kib': (0, 0x1fff7a22, 2), 'debug_idcode': (0, 0xe0042000, 4),
          'option_bytes': (1, 0x1fffc000, 16)}


def status(handle):
  raw = bytes(handle.controlRead(0xa1, 3, 0, 0, 6, timeout=2000))
  if len(raw) != 6:
    raise ValueError('short DFU status')
  return raw


def idle(handle):
  current = status(handle)
  if current[4] == 10:  # Clear transaction error only, NOT readout protection.
    handle.controlWrite(0x21, 4, 0, 0, b'', timeout=2000)
  handle.controlWrite(0x21, 6, 0, 0, b'', timeout=2000)
  current = status(handle)
  if current[0] or current[4] != 2:
    raise ValueError('ROM DFU not idle')


def read_field(handle, name):
  alt, address, length = FIELDS[name]  # No user-supplied address/length.
  idle(handle)
  handle.setInterfaceAltSetting(0, alt)
  handle.controlWrite(0x21, 1, 0, 0, b'\x21' + address.to_bytes(4, 'little'), timeout=2000)
  for _ in range(10):
    current = status(handle)
    if current[0]:
      raise ValueError('ROM rejected read address')
    if current[4] == 5:
      break
    delay = int.from_bytes(current[1:4], 'little')
    if current[4] not in (3, 4) or delay > 1000:
      raise ValueError('unexpected DFU pointer transition: ' + current.hex())
    time.sleep(max(0.001, delay / 1000))
  else:
    raise ValueError('DFU pointer deadline')
  idle(handle)
  result = bytes(handle.controlRead(0xa1, 2, 2, 0, length, timeout=2000))
  if len(result) != length:
    raise ValueError('short DFU upload')
  idle(handle)
  return result


def decode_options(raw):
  if len(raw) != 16 or raw[:4] != raw[4:8] or raw[8:12] != raw[12:16]:
    raise ValueError('unrecognized F4 option-byte mirrors')
  if any(raw[i] ^ raw[i+2] != 255 for i in (0, 1, 8, 9)):
    raise ValueError('option-byte complements differ')
  rdp = raw[1]
  return {'rdp_byte': rdp, 'rdp_level': 0 if rdp == 0xaa else 2 if rdp == 0xcc else 1,
          'user_byte': raw[0], 'protection_halfword_raw': int.from_bytes(raw[8:10], 'little'),
          'protection_bits_interpreted': False}


def inspect():
  import usb1
  with usb1.USBContext() as ctx:
    matches = []
    for device in ctx.getDeviceList(skip_on_error=False):
      if (device.getVendorID(), device.getProductID()) == (0x0483, 0xdf11):
        with closing(device.open()) as h:
          if h.getASCIIStringDescriptor(device.getSerialNumberDescriptor()) == SERIAL:
            matches.append(device)
    if len(matches) != 1:
      raise RuntimeError('exact labeled Panda ROM DFU not present')
    report = {'dfu_serial': SERIAL, 'programmed': False, 'option_bytes_changed': False, 'fields': {}}
    with closing(matches[0].open()) as h:
      h.claimInterface(0)
      try:
        for name in FIELDS:
          try:
            raw = read_field(h, name)
            report['fields'][name] = {'raw_hex': raw.hex(), 'value': decode_options(raw) if name == 'option_bytes' else int.from_bytes(raw, 'little')}
          except (ValueError, usb1.USBError) as e:
            report['fields'][name] = {'unavailable': type(e).__name__, 'reason': str(e)}
      finally:
        idle(h)
        h.setInterfaceAltSetting(0, 0)
        h.releaseInterface(0)
    return report


if __name__ == '__main__':
  print(json.dumps(inspect(), indent=2))
