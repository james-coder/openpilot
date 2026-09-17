import struct
from types import SimpleNamespace

import pytest

from openpilot.tools.volt_gateway import device_cli, startup_probe, usb_transport


@pytest.mark.parametrize('transmitted', [0, 1])
@pytest.mark.parametrize('rx_health', [False, True])
def test_white_probe_only_queries_and_closes(monkeypatch, transmitted, rx_health):
  calls = []
  reports = []
  class Transport:
    def __init__(self, serial):
      assert serial == '01' * 12
    def __enter__(self):
      return self
    def __exit__(self, *args):
      pass
  class Device:
    def __init__(self, *args):
      pass
    def command(self, op, payload=b''):
      calls.append(op)
      if op == 1:
        return b'\1\1' + (32 if rx_health else 0).to_bytes(4, 'big') + bytes(32) + bytes([3, 3, 0]) + bytes(5)
      if op == 14:
        return b'\0'
      if op == 2:
        return struct.pack('>Q6I', 1000, 0, 0, 0, 0, 0, 0)
      if op == 16:
        assert rx_health and payload in (b'\0', b'\1', b'\3')
        return struct.pack('>4I', 0, 5, 1, 2)
      assert op == 3 and payload in (b'\0', b'\1', b'\3')
      return struct.pack('>14I', 5, 0, 0, transmitted, *([0]*10))
    def close(self):
      calls.append('close')
  ticks = iter([0, 0, 2])
  monkeypatch.setattr(startup_probe.time, 'monotonic', lambda: next(ticks))
  monkeypatch.setattr(startup_probe.time, 'sleep', lambda _: None)
  monkeypatch.setattr(startup_probe, 'emit', lambda side, **kw: reports.append(kw))
  monkeypatch.setattr(device_cli, 'credentials', lambda _: {'device': b'\1'*12})
  monkeypatch.setattr(device_cli, 'Device', Device)
  monkeypatch.setattr(usb_transport, 'UsbTransport', Transport)
  if transmitted:
    with pytest.raises(ValueError, match='unexpected TX'):
      startup_probe.white(1, None)
  else:
    startup_probe.white(1, None)
    assert reports[-1]['event'] == 'finished'
    assert ('software_drops' in reports[1]['buses'][0]) == rx_health
  assert calls[-1] == 'close' and set(calls) <= {1, 2, 3, 14, 16, 'close'}


def test_tres_uses_existing_stream_and_excludes_echoes(monkeypatch):
  import sys
  def frame(bus):
    return SimpleNamespace(src=bus, address=0x460)
  event = SimpleNamespace(valid=True, can=[frame(0), frame(1), frame(2), frame(128), frame(192)])
  messaging = SimpleNamespace(sub_sock=lambda name, timeout: name, recv_one=lambda sock: event)
  monkeypatch.setitem(sys.modules, 'cereal', SimpleNamespace(messaging=messaging))
  ticks = iter([0, 0, 1.1, 3])
  monkeypatch.setattr(startup_probe.time, 'monotonic', lambda: next(ticks))
  reports = []
  monkeypatch.setattr(startup_probe, 'emit', lambda side, **kw: reports.append(kw))
  startup_probe.tres(2)
  assert reports[-1]['rx'] == {0: 1, 1: 1, 2: 1}
  assert reports[-1]['ids'] == {0: [0x460], 1: [0x460], 2: [0x460]}
