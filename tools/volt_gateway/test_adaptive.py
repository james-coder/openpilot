from dataclasses import replace

import pytest

from openpilot.tools.volt_gateway.adaptive import AdaptiveBudget, ChunkTransfer, Conditions


READY = Conditions(True, True, True, True, True, True, False)


def warm(budget, count=600, bits=2000):
  for i in range(count):
    budget.sample(i * .02, bits, READY)
  return (count - 1) * .02


def test_hard_cap_burst_and_staleness():
  b = AdaptiveBudget(hard_fps=20)
  now = warm(b)
  assert b.rate == 20
  assert b.take(now)
  assert b.take(now)
  assert not b.take(now)
  assert not b.take(now + .031)
  assert b.rate == 0


@pytest.mark.parametrize('field', ['stationary', 'offroad', 'power_stable', 'vehicle_awake', 'authenticated', 'compatible', 'errors'])
def test_every_gate_pauses_immediately(field):
  b = AdaptiveBudget()
  now = warm(b)
  b.sample(now + .02, 2000, replace(READY, **{field: field == 'errors'}))
  assert b.rate == 0
  assert not b.take(now + .02)


def test_burst_pause_and_clean_recovery():
  b = AdaptiveBudget()
  now = warm(b)
  b.sample(now + .02, 8000, READY)
  assert b.rate == 0
  for i in range(1, 50):
    b.sample(now + .02 + i * .02, 2000, READY)
    assert b.rate == 0
  b.sample(now + 1.02, 2000, READY)
  assert 0 < b.rate <= .1


def test_trend_backoff_gap_and_invalid_measurements():
  b = AdaptiveBudget()
  now = warm(b)
  before = b.rate
  for i in range(1, 6):
    b.sample(now + i * .02, 6400, READY)
  assert b.rate < before
  b.sample(now + .5, 2000, READY)
  assert b.rate == 0
  assert len(b.samples) == 1
  b.sample(now + .52, 10001, READY)
  assert not b.samples
  assert not b.take(now + .52)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1.])
def test_invalid_clock_fail_closed(bad):
  b = AdaptiveBudget()
  warm(b)
  with pytest.raises(ValueError):
    b.take(bad)
  assert b.rate == 0


def test_long_run_budget_and_bounded_history():
  b = AdaptiveBudget(hard_fps=20)
  sent = 0
  for i in range(20_000):
    now = i * .001
    if i % 20 == 0:
      b.sample(now, 2000, READY)
    sent += b.take(now)
  assert 0 < sent <= 20 * 20 + 2
  assert len(b.samples) == 50


def test_numbered_ack_and_completion():
  transfer = ChunkTransfer(300)
  transfer.sent(1)
  assert not transfer.acknowledge(300, 2)
  assert transfer.acknowledge(256, 2)
  assert not transfer.acknowledge(256, 2)
  transfer.sent(3)
  assert transfer.acknowledge(300, 4)
  with pytest.raises(ValueError):
    transfer.sent(5)


def test_bounded_retries_no_skipping():
  transfer = ChunkTransfer(512)
  for attempt in range(4):
    now = attempt * 6.
    transfer.sent(now)
    assert not transfer.timeout(now + 4)
    assert not transfer.acknowledge(256, now + 5)
    assert transfer.timeout(now + 5) == (attempt < 3)
    assert transfer.offset == 0
  assert transfer.failed
  with pytest.raises(ValueError):
    transfer.sent(30)
