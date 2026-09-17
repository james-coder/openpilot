import pytest

from openpilot.common.pid import PIDController
from openpilot.system.hardware.fan_controller import FanController


def test_high_temperature_headroom_and_slew(monkeypatch):
  monkeypatch.setattr('openpilot.system.hardware.fan_controller.OFFSET', 5)
  fan = FanController(2)
  assert fan.update(54, False) == 0
  assert fan.update(55, False) == 0
  assert fan.update(65, False) == 0
  first = fan.update(75, False)
  assert first <= 26
  for _ in range(50):
    result = fan.update(75, False)
  assert result == 40
  for _ in range(50):
    result = fan.update(50, False)
  assert result == 0


def test_disabled_and_other_hardware_unchanged(monkeypatch):
  monkeypatch.setattr('openpilot.system.hardware.fan_controller.OFFSET', 5)
  fan = FanController(2)
  assert fan.update(55, False, parked_cooling=False) == 0
  monkeypatch.setattr('openpilot.system.hardware.fan_controller.OFFSET', 0)
  assert fan.update(55, False) == 0


@pytest.mark.parametrize('enabled', [False, True])
def test_original_onroad_output_exact_and_offroad_never_reduced(monkeypatch, enabled):
  monkeypatch.setattr('openpilot.system.hardware.fan_controller.OFFSET', 5)
  fan = FanController(2)
  original = PIDController(k_p=0, k_i=4e-3, rate=2)
  last_ignition = False
  for ignition in [False, True, False, True]:
    for temp in list(range(30, 111)) + list(range(110, 29, -1)):
      original.pos_limit = 100 if ignition else 30
      original.neg_limit = 30 if ignition else 0
      if last_ignition != ignition:
        original.reset()
      last_ignition = ignition
      expected = int(original.update(error=temp-80, feedforward=max(0, min(100, (temp-65)*2.5))))
      actual = fan.update(temp, ignition, parked_cooling=enabled)
      if ignition or not enabled:
        assert actual == expected
      else:
        assert expected <= actual <= 40
