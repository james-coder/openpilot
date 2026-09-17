import socket
import time
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.car.volt_gateway_mailbox import GatewayMailbox, ADDRESS


@pytest.fixture
def mailbox():
  box = GatewayMailbox()
  assert box.sock is not None
  try:
    yield box
  finally:
    box.close()


def send(data):
  with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as peer:
    peer.sendto(data, ADDRESS)


def test_absent_and_stopped_client_nonblocking(mailbox):
  for safe in (False, True):
    assert mailbox.step(safe) == []
  send(time.monotonic_ns().to_bytes(8, 'big')+bytes(8))
  assert mailbox.step(True) == [(0x6F0, bytes(8), 1)]
  assert mailbox.step(True) == []  # peer has closed; nothing repeated


@pytest.mark.parametrize('safe,age,length', [(False, 0, 16), (True, -1, 16), (True, 100_000_000, 16),
                                           (True, 0, 15), (True, 0, 17), (True, 0, 100)])
def test_unsafe_stale_or_malformed_dropped(mailbox, safe, age, length):
  now = time.monotonic_ns()
  send(((now-age).to_bytes(8, 'big')+bytes(100))[:length])
  assert mailbox.step(safe, now) == []


def test_permission_denied_disables_only_mailbox(monkeypatch):
  def denied(*args):
    raise PermissionError
  monkeypatch.setattr(socket, 'socket', denied)
  box = GatewayMailbox()
  assert box.step(True) == []


def test_cold_boot_no_client_and_closed_socket(mailbox):
  assert mailbox.step(False) == []
  mailbox.sock.close()  # simulate unavailable socket / optional component fault
  assert mailbox.step(True) == []
  assert mailbox.sock is None


def test_other_uid_and_backlog(mailbox, monkeypatch):
  now = time.monotonic_ns()
  for _ in range(3):
    send(now.to_bytes(8, 'big')+bytes(8))
  with monkeypatch.context() as context:
    context.setattr('os.getuid', lambda: -1)
    assert mailbox.step(True, now) == []
  assert len(mailbox.step(True, now)) == 1
  assert mailbox.step(False, now) == []
  assert mailbox.step(True, now) == []


@pytest.mark.parametrize('engaged', [False, True])
@pytest.mark.parametrize('fault', [False, True])
def test_optional_mailbox_does_not_gate_controls_step(engaged, fault):
  from openpilot.selfdrive.car.card import Car, car
  class SM(dict):
    seen = {'onroadEvents': True}
    def all_checks(self, services):
      return True
  calls = []
  cs = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0)
  instance = Car.__new__(Car)
  instance.CP = SimpleNamespace(passive=False, carFingerprint='CHEVROLET_VOLT',
    safetyConfigs=[SimpleNamespace(safetyModel=car.CarParams.SafetyModel.gm, safetyParam=68)])
  instance.sm = SM(onroadEvents=[], carControl=SimpleNamespace(enabled=engaged),
                   pandaStates=[SimpleNamespace(controlsAllowed=engaged)])
  instance.state_update = lambda: (cs, None)
  instance.state_publish = lambda *args: None
  instance.controls_update = lambda *args: calls.append(engaged)
  instance.obd_scanner = SimpleNamespace(step=lambda *args: [])
  instance._obd_can_batches = []
  def fail(safe):
    assert safe is not engaged
    raise PermissionError('simulated optional mailbox failure')
  instance.gateway_mailbox = SimpleNamespace(step=fail) if fault else None
  for _ in range(2):
    instance.step()
  assert calls == [engaged, engaged]
  assert instance.gateway_mailbox is None
