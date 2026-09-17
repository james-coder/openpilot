"""Leave exact Panda ROM DFU via historical non-programming jump sequence."""

from contextlib import closing
import time

import usb1


def leave():
  with usb1.USBContext() as context:
    matches = []
    for device in context.getDeviceList(skip_on_error=False):
      if (device.getVendorID(), device.getProductID()) == (0x0483, 0xdf11):
        with closing(device.open()) as handle:
          if handle.getASCIIStringDescriptor(device.getSerialNumberDescriptor()) == '365236793036':
            matches.append(device)
    if len(matches) != 1:
      raise RuntimeError('Exact ROM DFU target not present')
    with closing(matches[0].open()) as handle:
      handle.claimInterface(0)
      handle.setInterfaceAltSetting(0, 0)
      # Software-entered STM ROM may initially report errFIRMWARE/dfuERROR.
      # CLRSTATUS clears only the DFU protocol error; it does not erase flash
      # or alter readout protection. Never clear an unexpected active state.
      status = bytes(handle.controlRead(0xa1, 3, 0, 0, 6, timeout=2000))
      if len(status) != 6:
        raise RuntimeError('Invalid initial DFU status')
      if status[4] == 10:
        handle.controlWrite(0x21, 4, 0, 0, b'', timeout=2000)
      # Abort the previous read transaction; never clear protection or flash.
      handle.controlWrite(0x21, 6, 0, 0, b'', timeout=2000)
      status = bytes(handle.controlRead(0xa1, 3, 0, 0, 6, timeout=2000))
      if len(status) != 6 or status[0] != 0 or status[4] != 2:
        raise RuntimeError('DFU not idle; no jump attempted')
      # Set ROM's address pointer to the preserved original bootstub.
      handle.controlWrite(0x21, 1, 0, 0, b'\x21\x00\x00\x00\x08', timeout=2000)
      for _ in range(10):
        status = bytes(handle.controlRead(0xa1, 3, 0, 0, 6, timeout=2000))
        if len(status) != 6 or status[0] != 0:
          raise RuntimeError('DFU error; no jump attempted')
        if status[4] == 5:
          break
        if status[4] not in (3, 4):
          raise RuntimeError('Unexpected DFU state')
        delay_ms = int.from_bytes(status[1:4], 'little')
        if delay_ms > 1000:
          raise RuntimeError('Unexpected DFU wait')
        time.sleep(max(.001, delay_ms / 1000))
      else:
        raise RuntimeError('DFU pointer setup deadline')
      # Zero-length DNLOAD manifests/jumps; no firmware payload is supplied.
      handle.controlWrite(0x21, 1, 2, 0, b'', timeout=2000)
      try:
        handle.controlRead(0xa1, 3, 0, 0, 6, timeout=2000)
      except (usb1.USBErrorNoDevice, usb1.USBErrorIO):
        pass
      print('Non-programming DFU jump requested; verify original application enumeration.')


if __name__ == '__main__':
  leave()
