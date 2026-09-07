import pytest
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import CanSnapshot, GraphBuffer, diagnostics_timeout, graph_display_bounds


def test_units_and_named_states_keep_numeric_graph_values():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  packer = CANPacker('gm_global_a_powertrain_generated')
  frames = [packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': 2}),
            packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1260})]
  snap.ingest([(1000, frames)])
  gear = snap.rows[(0, 309, 'PRNDL')]
  assert gear.text == '2 (D)'
  assert gear.value == 2
  assert 'ECMPRDNL' in gear.details()
  rpm = snap.rows[(0, 201, 'EngineRPM')]
  assert rpm.text == '1260 RPM'
  assert rpm.value == 1260


def test_parked_inspection_timeout():
  assert diagnostics_timeout(False, False, 0, False) == 300
  assert diagnostics_timeout(True, False, 0, True) == 300


@pytest.mark.parametrize('enabled,speed,valid', [(True, 0, True), (False, 0, False), (False, 1, True),
                                               (False, -1, True), (False, float('nan'), True)])
def test_normal_timeout_when_moving_engaged_or_vehicle_state_unavailable(enabled, speed, valid):
  assert diagnostics_timeout(True, enabled, speed, valid) is None


def test_flat_graph_is_centered_and_buffer_is_bounded():
  lo, hi = graph_display_bounds(2, 2)
  assert lo < 2 < hi
  assert 2-lo == pytest.approx(hi-2)
  buf = GraphBuffer()
  for i in range(7000):
    buf.add(i/1000, 2)
  assert len(buf.samples) == 6000
  buf.add(7, float('nan'))
  buf.add(float('inf'), 3)
  assert buf.bounds() == (2, 2)
