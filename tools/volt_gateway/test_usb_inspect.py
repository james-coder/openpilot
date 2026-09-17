import types
import struct

import pytest

from openpilot.tools.volt_gateway.usb_inspect import decode_legacy_health, inspect


SERIAL = '370022000651363038363036'


class FakeDevice:
  def __init__(self):
    self.requests = []

  def getVendorID(self):
    return 0xbbaa

  def getProductID(self):
    return 0xddcc

  def getSerialNumberDescriptor(self):
    return 3

  def getBusNumber(self):
    return 1

  def getDeviceAddress(self):
    return 2

  def open(self):
    return self

  def close(self):
    pass

  def getASCIIStringDescriptor(self, index):
    assert index == 3
    return SERIAL

  def controlRead(self, request_type, request, value, index, size, timeout):
    self.requests.append((request_type, request, value, index, size, timeout))
    if request == 0xd2:
      return struct.pack('<8I9B', *([0] * 17))
    return b'v1.7.3-EON-unknown-RELEASE' if request == 0xd6 else b'\x01'


def setup_usb(monkeypatch, devices):
  import usb1

  class Context:
    def __enter__(self):
      return types.SimpleNamespace(getDeviceList=lambda **kwargs: devices)

    def __exit__(self, *args):
      pass

  monkeypatch.setattr(usb1, 'USBContext', Context)


def test_only_in_requests_to_exact_serial(monkeypatch):
  device = FakeDevice()
  setup_usb(monkeypatch, [device])
  result = inspect(SERIAL)
  assert result['version'] == 'v1.7.3-EON-unknown-RELEASE'
  assert result['hardware_type_hex'] == '01'
  assert not result['device_changed']
  assert device.requests == [(0xc0, 0xd6, 0, 0, 64, 2000), (0xc0, 0xc1, 0, 0, 1, 2000)]


@pytest.mark.parametrize('count', [0, 2])
def test_no_arbitrary_device_selection(monkeypatch, count):
  devices = [FakeDevice() for _ in range(count)]
  setup_usb(monkeypatch, devices)
  with pytest.raises(RuntimeError):
    inspect(SERIAL)
  assert all(not d.requests for d in devices)


def test_mismatched_serial_not_queried(monkeypatch):
  device = FakeDevice()
  setup_usb(monkeypatch, [device])
  with pytest.raises(RuntimeError):
    inspect('0' * 24)
  assert not device.requests


def test_health_query_is_in_only(monkeypatch):
  device = FakeDevice()
  setup_usb(monkeypatch, [device])
  result = inspect(SERIAL, health=True)
  assert result['health']['safety_mode'] == 0
  assert all(request[0] == 0xc0 for request in device.requests)
  assert device.requests[-1][1] == 0xd2


def test_health_unknown_layout_or_boolean_rejected():
  with pytest.raises(ValueError):
    decode_legacy_health(bytes(40))
  raw = bytearray(41)
  raw[32] = 2
  with pytest.raises(ValueError):
    decode_legacy_health(raw)
