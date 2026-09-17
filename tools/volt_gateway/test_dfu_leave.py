from unittest.mock import MagicMock, patch

import pytest

from tools.volt_gateway.dfu_leave import leave


@pytest.mark.parametrize('initial', [bytes([0, 0, 0, 0, 2, 0]), bytes([10, 0, 0, 0, 10, 0])])
def test_leave_idle_or_software_entry_error(initial):
  handle = MagicMock()
  handle.getASCIIStringDescriptor.return_value = '365236793036'
  handle.controlRead.side_effect = [initial, bytes([0, 0, 0, 0, 2, 0]),
                                  bytes([0, 0, 0, 0, 5, 0]), bytes(6)]
  device = MagicMock()
  device.getVendorID.return_value = 0x0483
  device.getProductID.return_value = 0xdf11
  device.open.return_value = handle
  with patch('tools.volt_gateway.dfu_leave.usb1.USBContext') as context:
    context.return_value.__enter__.return_value.getDeviceList.return_value = [device]
    leave()
  writes = handle.controlWrite.call_args_list
  assert [call.args[1] for call in writes] == ([4] if initial[4] == 10 else []) + [6, 1, 1]
  assert writes[-2].args[4] == b'\x21\x00\x00\x00\x08'
  assert writes[-1].args[4] == b''
