import pytest

from openpilot.tools.volt_gateway.usb_mode import transition
from openpilot.tools.volt_gateway.test_usb_inspect import FakeDevice, setup_usb


class ModeDevice(FakeDevice):
  def __init__(self, pid, version=b'v1.7.3-EON-unknown-RELEASE'):
    super().__init__()
    self.pid, self.version, self.writes = pid, version, []

  def getProductID(self):
    return self.pid

  def controlRead(self, request_type, request, value, index, size, timeout):
    if request == 0xb0:
      return b'\xff\xff\xb0\x4f\xde\xad\xd0\x0d' + bytes(4)
    return self.version

  def controlWrite(self, *args, **kwargs):
    self.writes.append((args, kwargs))


@pytest.mark.parametrize('action,pid,req,value', [('softloader', 0xddcc, 0xd1, 1), ('rom', 0xddee, 0xd1, 0),
                                               ('application', 0xddee, 0xd8, 0)])
def test_exact_nonprogramming_transition(monkeypatch, action, pid, req, value):
  device = ModeDevice(pid)
  setup_usb(monkeypatch, [device])
  transition(action)
  assert device.writes == [((0x40, req, value, 0, b''), {'timeout': 2000})]


def test_unreviewed_version_never_resets(monkeypatch):
  device = ModeDevice(0xddcc, b'unknown')
  setup_usb(monkeypatch, [device])
  with pytest.raises(RuntimeError):
    transition('softloader')
  assert not device.writes
