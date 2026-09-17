from types import SimpleNamespace

import pytest

from openpilot.tools.volt_gateway.object_transport import ObjectTransport
from openpilot.tools.volt_gateway.protocol import ProtocolError


def setup():
  state = SimpleNamespace(now=0., safe=True, tx=[], batches=[])
  def sleep(seconds):
    state.now += seconds
  def receive():
    state.now += .001
    return state.batches.pop(0) if state.batches else []
  transport = ObjectTransport(lambda *args: state.tx.append((state.now, args)), receive,
                              lambda: state.safe, clock=lambda: state.now, sleep=sleep)
  return state, transport


def test_exact_tx_rate_and_total_budget():
  state, transport = setup()
  for _ in range(512):
    transport.send(bytes(8))
  assert all(item == (0x6F0, bytes(8), 1) for _, item in state.tx)
  assert all(b[0]-a[0] >= .012-1e-12 for a, b in zip(state.tx, state.tx[1:], strict=False))
  with pytest.raises(ProtocolError):
    transport.send(bytes(8))


def test_live_state_rechecked_after_wait():
  state, transport = setup()
  transport.send(bytes(8))
  def unsafe(_):
    state.safe = False
  transport.sleep = unsafe
  with pytest.raises(ProtocolError):
    transport.send(bytes(8))
  assert len(state.tx) == 1


def test_rx_filters_and_timeout():
  state, transport = setup()
  frame = b'\x01a'+bytes(6)
  state.batches = [[(0x6F1, frame, bus) for bus in (0, 2, 129, 193)]+[(0x6F0, frame, 1)], [(0x6F1, frame, 1)]]
  assert transport.receive(.1) == frame
  with pytest.raises(TimeoutError):
    transport.receive(.01)


@pytest.mark.parametrize('data,count', [(bytes(7), 1), (bytes(8), 65)])
def test_bounded_queue_and_dlc(data, count):
  state, transport = setup()
  state.batches = [[(0x6F1, data, 1)]*count]
  with pytest.raises(ProtocolError):
    transport.receive(1)


def test_trial_expiry_and_unavailable_state():
  state, transport = setup()
  state.now = 60
  with pytest.raises(ProtocolError):
    transport.send(bytes(8))
  state.now = 0
  state.safe = False
  with pytest.raises(ProtocolError):
    transport.receive(1)
  assert not state.tx
