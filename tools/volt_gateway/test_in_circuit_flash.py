import pytest

from openpilot.tools.volt_gateway.in_circuit_flash import request_recovery


class Device:
  def __init__(self,info):
    self.info=info
    self.calls=[]

  def command(self,op):
    self.calls.append(op)
    return self.info if op==1 else b''


def test_no_actuation_update_gate_or_flash_opcode_in_recovery_request():
  info=bytearray(46)
  info[:2]=b'\1\1'
  info[2:6]=(128).to_bytes(4,'big')
  device=Device(bytes(info))
  request_recovery(device)
  assert device.calls==[1,20]


@pytest.mark.parametrize('info',[b'',bytes(46),b'\1\1'+bytes(44),b'\1\1\0\0\0\x80'])
def test_old_or_invalid_firmware_never_requested_to_reset(info):
  device=Device(info)
  with pytest.raises(RuntimeError):
    request_recovery(device)
  assert device.calls==[1]
