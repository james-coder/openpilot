import json
from pathlib import Path

import pytest

from openpilot.tools.volt_gateway.traffic_compare import driving_state, recent
from openpilot.tools.volt_gateway.traffic_replay import frame_bits, replay


def test_stuffing_bounds():
  assert frame_bits(0x123, 8) == 135
  assert frame_bits(0x12345, 8) == 160
  assert frame_bits(0x123, 0) == 55
  with pytest.raises(ValueError):
    frame_bits(0x123, 9)


def test_seeded_load_replay_is_reproducible_and_bounded():
  first = replay([2400] * 3000)
  assert first == replay([2400] * 3000)
  assert first['max_queued_packets'] <= 17
  assert first['completed_control_responses'] == 60
  assert first['scheduler_counters']['telemetry_dropped'] > 0
  assert first['scheduler_tx_frames'] <= 40 * 60 + 2
  assert not first['hardware_emulation']


def test_full_bus_denies_all_modeled_transmission():
  report = replay([10000] * 100)
  assert report['scheduler_tx_frames'] == 0
  assert report['update_admitted_frames'] == 0


def test_batched_impossible_load_not_silently_smoothed():
  report = replay([12000] * 100)
  assert report['bins_exceeding_physical_capacity'] == 100
  assert report['budget_reasons']['invalid measurement'] == 100


def test_fault_scenario_remains_bounded():
  report = replay([2400] * 3000, fault=True)
  assert report['max_queued_packets'] <= 17
  assert report['budget_reasons']['conditions failed'] > 0
  assert report['completed_control_responses'] >= 58


@pytest.mark.parametrize('speed,active,expected', [
  (None, False, 'unknown'), (0, None, 'unknown'), (0, False, 'stationary_inactive'),
  (0, True, 'stationary_active'), (.5, False, 'creeping_inactive'),
  (30, True, 'driving_active'), (30, False, 'driving_inactive'), (-2, False, 'driving_inactive'),
])
def test_state_classification(speed, active, expected):
  assert driving_state(speed, active) == expected


def test_cross_service_timestamp_order_does_not_use_future_state():
  assert recent([(100, False), (120, True)], 110) is False
  assert recent([(100, False), (120, True)], 120) is True
  assert recent([(100, False)], 1_000_000_100) is None


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('fault', [False, True])
def test_real_object_bus_load_fixture(index, fault):
  fixture = json.loads((Path(__file__).parent / 'fixtures/object-bus-load.json').read_text())
  case = fixture['cases'][index]
  result = replay(case['window_bits'], fault=fault)
  assert result['max_queued_packets'] <= 17
  assert result['scheduler_tx_frames'] <= 40 * result['seconds'] + 2
  assert result['completed_control_responses'] >= 55
  assert result['scheduler_counters']['telemetry_dropped'] > 0
  assert not result['hardware_emulation']
