import pytest
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  BusStats, CanSnapshot, GraphBuffer, MAX_RAW_MESSAGES_PER_BUS, diagnostics_timeout, graph_display_bounds, matches_query,
)


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


def test_catalog_is_available_without_traffic_and_coverage_does_not_invent_observations():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  assert snap.catalog(0, 'PRNDL')[0].name == 'ECMPRDNL'
  assert all(d.bus == 1 for d in snap.catalog(1))
  assert all(not row['alive'] and row['observed'] == row['decoded'] == 0 for row in snap.coverage())
  assert all(row['defined_messages'] > 0 and row['defined_signals'] > 0 for row in snap.coverage())
  key = (0, 309, 'PRNDL')
  assert snap.definition_row(key).text == 'Not seen'
  assert 'raw *' in snap.signal_details(key) and '2=D' in snap.signal_details(key)


@pytest.mark.parametrize('query', ['0135', '0x135', '0x0135', '309', 'prndl', 'powertrain prndl'])
def test_search_handles_names_and_exact_hex_or_decimal_ids(query):
  assert matches_query(query, 0, 309, 'ECMPRDNL', 'PRNDL')
  assert not matches_query('0x135', 0, 0x1350, 'Unrelated')
  assert not matches_query('radar prndl', 0, 309, 'ECMPRDNL')


def test_coverage_is_bus_specific_and_separates_matched_from_successfully_decoded():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  packer = CANPacker('gm_global_a_powertrain_generated')
  frame = packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': 2})
  snap.ingest([(1000, [frame, (frame[0], frame[1], 1), (201, b'\x00'*8, 1)])])
  powertrain, radar, chassis = snap.coverage()
  assert powertrain['observed'] == powertrain['matched'] == powertrain['decoded'] == 1
  assert radar['observed'] == 2 and radar['matched'] == 1 and radar['unknown'] == 1 and radar['decoded'] == 0
  assert chassis['observed'] == 0


def test_wrong_length_never_creates_a_plausible_decoded_value_and_recovers():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  snap.ingest([(1000, [(309, b'\x02', 0)])])
  row = snap.coverage()[0]
  assert row['matched'] == 1 and row['decoded'] == 0
  assert (0, 309, 'PRNDL') not in snap.rows
  assert 'expected' in snap.rows[(0, 309, None)].details()
  frame = CANPacker('gm_global_a_powertrain_generated').make_can_msg('ECMPRDNL', 0, {'PRNDL': 2})
  touched = snap.ingest([(2000, [frame])])
  assert snap.coverage()[0]['decoded'] == 1
  assert (0, 309, None) in touched and (0, 309, None) not in snap.rows
  assert snap.rows[(0, 309, 'PRNDL')].value == 2


def test_changed_filter_counts_real_value_changes_and_keeps_graph_numeric():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  packer = CANPacker('gm_global_a_powertrain_generated')
  key = (0, 201, 'EngineRPM')
  for i, rpm in enumerate((1200, 1200, 1400, 1400)):
    snap.ingest([(1000+i, [packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': rpm})])])
  assert snap.rows[key].changes == 1 and snap.rows[key].value == 1400


def test_unknown_extended_ids_cannot_grow_the_inspector_forever():
  snap = CanSnapshot(CAR.CHEVROLET_VOLT)
  snap.ingest([(1000, [(0x10000+i, b'\x01', 0) for i in range(1000)])])
  assert len(snap.messages) == len(snap.rows) == MAX_RAW_MESSAGES_PER_BUS
  assert snap.coverage()[0]['omitted_raw_frames'] == 1000-MAX_RAW_MESSAGES_PER_BUS


def test_rate_and_quiet_bus_keep_counts_but_not_a_fake_live_rate():
  stats = BusStats(count=10, last_seen=10.)
  assert stats.rate(10.) == 0
  stats.count, stats.last_seen = 30, 10.5
  assert stats.rate(10.5) == pytest.approx(40)
  assert stats.rate(12.) == 0 and not stats.alive(12.)
  for i in range(1000):
    stats.rate(12.+i)
  assert len(stats.rate_samples) <= 32
