import dataclasses
from types import SimpleNamespace

import pytest
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings.can_inspection import (
  BitTracker, InspectionSession, bit_definitions, preference_document, sample_at, signal_bits, validated_favorites,
)
from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import GraphBuffer

GEAR = (0, 309, 'PRNDL')
RPM = (0, 201, 'EngineRPM')
TPS = (0, 201, 'EngineTPS')


def make_frame(gear):
  return CANPacker('gm_global_a_powertrain_generated').make_can_msg('ECMPRDNL', 0, {'PRNDL': gear})


@pytest.fixture
def session():
  return InspectionSession(CAR.CHEVROLET_VOLT)


def test_favorite_roundtrip_order_and_per_car_identity(session):
  for key in (RPM, GEAR):
    assert session.toggle_favorite(key)
  document = preference_document(session.fingerprint, session.favorites)
  loaded = InspectionSession(session.fingerprint, document)
  assert loaded.favorites == [RPM, GEAR]
  assert validated_favorites(document, 'OTHER_CAR', session.snapshot.metadata) == []
  assert loaded.toggle_favorite(RPM) and loaded.favorites == [GEAR]


@pytest.mark.parametrize('document', [None, [], 12, 'oops', {'version': 2}, {'version': 1, 'cars': []},
                                     {'version': 1, 'cars': {CAR.CHEVROLET_VOLT: ['x', {}, [True, 309, 'PRNDL'], [0, 309, 'missing']]}}])
def test_bad_preferences_are_ignored(document, session):
  assert validated_favorites(document, session.fingerprint, session.snapshot.metadata) == []


def test_favorites_are_bounded_and_reject_raw_keys(session):
  keys = list(session.snapshot.metadata)
  for key in keys[:24]:
    assert session.toggle_favorite(key)
  assert not session.toggle_favorite(keys[24])
  assert not session.toggle_favorite((0, 0x7ff, None))
  assert len(session.favorites) == 24


def test_baseline_detects_change_and_return_even_in_one_can_batch(session):
  session.ingest([(1_000_000_000, [make_frame(2)])])
  session.reset_baseline()
  session.ingest([(2_000_000_000, [make_frame(3), make_frame(2)])])
  assert GEAR in session.changed
  assert session.snapshot.rows[GEAR].value == 2
  session.reset_baseline()
  assert not session.changed and not session.new
  session.ingest([(3_000_000_000, [make_frame(2)])])
  assert GEAR not in session.changed


def test_new_signals_and_raw_byte_changes(session):
  session.ingest([(1, [(0x7ff, b'\x00', 0)])])
  key = (0, 0x7ff, None)
  assert key in session.new
  session.reset_baseline()
  session.ingest([(2, [(0x7ff, b'\x01', 0), (0x7ff, b'\x00', 0)])])
  assert key in session.changed


def test_freeze_is_immutable_and_preserves_counts_bits_and_history(session):
  session.ingest([(1_000_000_000, [make_frame(2)])])
  session.inspect_bits((0, 309))
  session.select_graph(GEAR)
  frozen = session.toggle_freeze()
  before = frozen.messages[(0, 309)].count
  session.ingest([(2_000_000_000, [make_frame(3)])])
  session.reset_baseline()  # Disabled while frozen.
  assert session.capture() is frozen
  assert frozen.rows[GEAR].value == 2
  assert frozen.messages[(0, 309)].count == before
  assert frozen.bits.flips[0] == 0
  assert session.snapshot.rows[GEAR].value == 3
  with pytest.raises(TypeError):
    frozen.rows[GEAR] = None
  with pytest.raises(dataclasses.FrozenInstanceError):
    frozen.rows[GEAR].value = 9
  assert not session.select_graph(RPM)
  session.toggle_freeze()
  assert session.capture().rows[GEAR].value == 3


def test_two_graphs_max_and_replacing_one_keeps_the_other(session):
  session.select_graph(RPM, 0)
  session.select_graph(TPS, 1)
  buffer = session.histories[TPS]
  session.select_graph(GEAR, 0)
  assert list(session.histories) == [GEAR, TPS]
  assert session.histories[TPS] is buffer
  assert not session.select_graph(RPM, 2)


def test_selected_bits_use_dbc_motorola_order_and_leave_unknown_bits_undefined(session):
  bits = bit_definitions(session.snapshot, (0, 309))
  assert all('PRNDL' in bits[i] for i in (2, 1, 0))
  assert 'PRNDL' not in bits.get(3, ())
  assert bit_definitions(session.snapshot, (0, 0x7ff)) == {}


@pytest.mark.parametrize('little,start,size,expected', [(True, 6, 5, (6, 7, 8, 9, 10)),
                                                      (False, 2, 6, (2, 1, 0, 15, 14, 13)),
                                                      (False, 15, 9, (15, 14, 13, 12, 11, 10, 9, 8, 23))])
def test_cross_byte_bit_mapping(little, start, size, expected):
  assert signal_bits(SimpleNamespace(is_little_endian=little, start_bit=start, size=size)) == expected


def test_bit_tracker_counts_every_frame_and_length_changes_without_inventing_flips():
  tracker = BitTracker((0, 1), b'\x00', now=1.)
  tracker.update(b'\x01', 2.)
  tracker.update(b'\x00\xff', 3.)
  assert tracker.flips[0] == 2
  assert tracker.flips[8] == 0
  tracker.update(bytes(64), 4.)
  assert tracker.flips[8] == 1
  assert len(tracker.flips) == len(tracker.changed_at) == 512
  assert tracker.freeze().changed_at[0] == 3.


def test_cursor_returns_real_previous_sample_and_late_samples_are_rejected():
  samples = [(1., 0.), (2., 4.), (5., 8.)]
  assert sample_at(samples, .5) is None
  assert sample_at(samples, 2.5) == (2., 4.)
  assert sample_at(samples, 4.) == (2., 4.)  # Caller must display the two-second age/gap.
  buf = GraphBuffer()
  buf.add(2., 4.)
  buf.add(1., 0.)
  assert list(buf.samples) == [(2., 4.)]


def test_frozen_receiver_still_updates_live_message_rate(session, monkeypatch):
  monkeypatch.setattr('time.monotonic', lambda: 10.)
  session.ingest([(10_000_000_000, [make_frame(2)])])
  session.toggle_freeze()
  monkeypatch.setattr('time.monotonic', lambda: 10.5)
  session.ingest([(10_500_000_000, [make_frame(2)]*20)])
  session.toggle_freeze()
  assert session.capture(now=10.5).messages[(0, 309)].rate == pytest.approx(40)
