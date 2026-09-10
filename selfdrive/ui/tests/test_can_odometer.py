from pathlib import Path

import pytest
from opendbc.can.dbc import DBC
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import (
  CanSnapshot, ODOMETER_KEY, ODOMETER_KM_KEY, ODOMETER_DBC, signal_timeout,
)
from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession, bit_definitions

# Real passive frames from two consecutive 5-second samples in this Volt's log.
FIRST = bytes.fromhex('00afde2d00')
NEXT = bytes.fromhex('00afde3700')
LATEST = bytes.fromhex('00b0d4db00')


def test_recorded_odometer_frame_units_precision_and_source():
  snapshot = CanSnapshot(CAR.CHEVROLET_VOLT)
  snapshot.ingest([(1, [(0x120, LATEST, 0)])])
  assert snapshot.rows[ODOMETER_KEY].value == pytest.approx(112515.05077534697)
  assert snapshot.rows[ODOMETER_KEY].text == '112,515.1 mi'
  assert snapshot.rows[ODOMETER_KM_KEY].value == pytest.approx(int.from_bytes(LATEST[:4], 'big')/64)
  assert snapshot.rows[ODOMETER_KM_KEY].unit == 'km'
  assert snapshot.messages[(0, 0x120)].decoded
  assert snapshot.message_dbcs[(0, 0x120)] == ODOMETER_DBC
  assert 'volt_odometer' in snapshot.signal_details(ODOMETER_KEY)
  assert snapshot.catalog(query='odometer')[0].address == 0x120
  assert snapshot.catalog(query='mi 0x120')[0].signals == ('OdometerKm', 'OdometerMiles')


def test_odometer_does_not_change_driving_dbc_or_other_cars():
  original = DBC('gm_global_a_powertrain_generated')
  addresses = set(original.msgs)
  snapshot = CanSnapshot(CAR.CHEVROLET_VOLT)
  assert (0, 0x120) in snapshot.definitions
  assert 0x120 not in snapshot.parsers[0].addresses
  assert set(original.msgs) == addresses and 0x120 not in original.msgs
  other = next(car for car in CAR if car != CAR.CHEVROLET_VOLT)
  other_snapshot = CanSnapshot(other)
  assert ODOMETER_KEY not in other_snapshot.metadata


def test_odometer_wrong_bus_is_not_decoded():
  snapshot = CanSnapshot(CAR.CHEVROLET_VOLT)
  snapshot.ingest([(1, [(0x120, LATEST, 1), (0x120, LATEST, 2)])])
  assert ODOMETER_KEY not in snapshot.rows


@pytest.mark.parametrize('bad', [b'', LATEST[:4], LATEST+b'\0', b'\xff'*5])
def test_bad_payload_removes_current_reading_and_recovers(bad):
  snapshot = CanSnapshot(CAR.CHEVROLET_VOLT)
  snapshot.ingest([(1, [(0x120, FIRST, 0)])])
  snapshot.ingest([(2, [(0x120, NEXT, 0), (0x120, bad, 0)])])
  assert ODOMETER_KEY not in snapshot.rows
  assert ODOMETER_KM_KEY not in snapshot.rows
  assert (0, 0x120, None) in snapshot.rows
  snapshot.ingest([(3, [(0x120, LATEST, 0)])])
  assert snapshot.rows[ODOMETER_KEY].value == pytest.approx(112515.05077534697)
  assert (0, 0x120, None) not in snapshot.rows


def test_zero_is_a_real_counter_and_fifth_byte_remains_undefined():
  snapshot = CanSnapshot(CAR.CHEVROLET_VOLT)
  snapshot.ingest([(1, [(0x120, bytes(5), 0)])])
  assert snapshot.rows[ODOMETER_KEY].value == 0
  mapping = bit_definitions(snapshot, (0, 0x120))
  assert set(mapping) == set(range(32))
  assert all(set(names) == {'OdometerMiles', 'OdometerKm'} for names in mapping.values())
  assert 'Fifth byte semantics not verified' in Path(ODOMETER_DBC).read_text()


def test_odometer_change_freeze_graph_and_slow_update_timeout(monkeypatch):
  clock = [10.]
  monkeypatch.setattr('time.monotonic', lambda: clock[0])
  session = InspectionSession(CAR.CHEVROLET_VOLT)
  session.ingest([(10_000_000_000, [(0x120, FIRST, 0)])])
  session.select_graph(ODOMETER_KEY)
  session.reset_baseline()
  frozen = session.toggle_freeze()
  clock[0] = 15.
  session.ingest([(15_000_000_000, [(0x120, NEXT, 0)])])
  assert session.capture() is frozen
  assert session.capture().rows[ODOMETER_KEY].value == pytest.approx(111901.93216925653)
  session.toggle_freeze()
  assert ODOMETER_KEY in session.capture().changed
  assert len(session.histories[ODOMETER_KEY].samples) == 2
  assert signal_timeout(ODOMETER_KEY) == signal_timeout(ODOMETER_KM_KEY) == 15
  assert signal_timeout((0, 201, 'EngineRPM')) == 1
