import json
import os

import cereal.messaging as messaging
import pytest

from opendbc.car.gm.values import CAR
from openpilot.selfdrive.car.gm_trip_data import TripEvidence
from openpilot.selfdrive.car.gm_trip_monitor import RoutePreserver, MIN_FREE_BYTES
from openpilot.tools.car_porting.gm_trip_report import index_route
from openpilot.tools.lib.logreader import save_log


def event(kind, t, size=None):
  msg = messaging.new_message(kind, size=size)
  msg.logMonoTime = round(t * 1e9)
  msg.valid = True
  return msg


def params():
  msg = event('carParams', 99)
  msg.carParams.carFingerprint = CAR.CHEVROLET_VOLT
  return msg


def engine(t, bus=0, address=0xC9):
  msg = event('can', t, 1)
  msg.can[0] = {'address': address, 'dat': bytes.fromhex('00 12 c0 00 10 01 00 00'), 'src': bus}
  return msg


def state(t, gas=False):
  msg = event('carState', t)
  msg.carState.canValid = True
  msg.carState.vEgo = 15
  msg.carState.aEgo = -.5
  msg.carState.gasPressed = gas
  return msg


def test_native_timestamp_signal_and_candidate_not_confirmed_event():
  data = TripEvidence()
  data.feed(params())
  for i in range(6):
    t = 100 + i / 10
    rows = data.feed(engine(t))
    assert rows[0]['values']['engine_rpm'] == 1200
    assert rows[0]['mono'] == t
    data.feed(state(t + .01))
  result = data.finish()
  assert len(result['candidate_windows']) == 1
  assert 'UNCONFIRMED' in result['candidate_windows'][0]['label']
  assert 'NOT confirmed throttle' in result['limitations']
  assert result['signals']['engine_rpm']['count'] == 6


def test_unknown_bus_frames_preserved_counted_not_named_egr():
  data = TripEvidence()
  data.feed(params())
  for bus in (0, 1, 2, 128):
    assert data.feed(engine(100, bus, 0x555)) == []
  result = data.finish()
  assert result['can_frames_by_bus'] == {'0': 1, '1': 1, '2': 1, '128': 1}
  assert not result['signals'] and not result['candidate_windows']


def test_no_vehicle_identity_stale_invalid_and_pressed_pedal_do_not_make_windows():
  data = TripEvidence()
  assert not data.feed(engine(100))
  data.feed(params())
  data.feed(engine(100))
  for t in (101, 101.1, 101.2):
    data.feed(state(t))
  for i in range(10):
    data.feed(engine(102 + i / 10))
    data.feed(state(102 + i / 10, gas=True))
  assert not data.finish()['candidate_windows']


def test_preserver_hardlinks_growing_raw_without_copy_and_survives_unlink(tmp_path):
  logs, pins = tmp_path / 'logs', tmp_path / 'pins'
  folder = logs / 'route--0'
  folder.mkdir(parents=True)
  source = folder / 'rlog.zst'
  source.write_bytes(b'raw including unknown buses')
  preserver = RoutePreserver(logs, pins, since=0)
  result = preserver.update(free_bytes=MIN_FREE_BYTES + 1)
  target = pins / folder.name / source.name
  assert os.path.samefile(source, target)
  with source.open('ab') as stream:
    stream.write(b' later CAN')
  assert target.read_bytes().endswith(b' later CAN')
  source.unlink()
  assert target.read_bytes().startswith(b'raw') and result['segments'] == 1
  assert json.loads((pins / 'status.json').read_text())['state'] == 'preserving'


def test_preserver_low_disk_does_not_pin_or_delete(tmp_path):
  logs = tmp_path / 'logs'
  logs.mkdir()
  preserver = RoutePreserver(logs, tmp_path / 'pins', since=0)
  assert preserver.update(free_bytes=0)['state'] == 'limited'
  assert logs.exists()


def test_full_route_index_keeps_unknown_frames_and_reports_missing_segments(tmp_path):
  folder = tmp_path / 'route--1'
  folder.mkdir()
  save_log(str(folder / 'rlog.zst'), [m.as_reader() for m in (params(), engine(100), engine(100.1, 2, 0x555), state(100.1))])
  report = index_route(tmp_path)
  assert report['missing_segments'] == [0]
  assert report['context']['can_frames_by_bus'] == {'0': 1, '2': 1}
  assert report['context']['signals']['engine_rpm']['count'] == 1
  assert len(report['segments'][0]['sha256']) == 64
  with pytest.raises(InterruptedError):
    index_route(tmp_path, cancelled=lambda: True)


def test_active_segment_and_qlogs_not_treated_as_complete(tmp_path):
  folder = tmp_path / 'route--0'
  folder.mkdir()
  save_log(str(folder / 'qlog.zst'), [params().as_reader()])
  with pytest.raises(ValueError):
    index_route(tmp_path)
  save_log(str(folder / 'rlog.zst'), [params().as_reader()])
  (folder / 'rlog.lock').touch()
  result = index_route(tmp_path)
  assert result['state'] == 'partial' and not result['segments']


def test_trip_process_only_volt_and_onroad(mocker):
  from openpilot.system.manager.process_config import volt_trip_logging
  cp = params().carParams
  cp.passive = False
  assert volt_trip_logging(True, mocker.Mock(), cp)
  assert not volt_trip_logging(False, mocker.Mock(), cp)
  cp.carFingerprint = 'another vehicle'
  assert not volt_trip_logging(True, mocker.Mock(), cp)


def test_unbounded_segment_number_rejected_before_range_allocation(tmp_path):
  folder = tmp_path / 'route--999999999'
  folder.mkdir()
  save_log(str(folder / 'rlog.zst'), [params().as_reader()])
  with pytest.raises(ValueError, match='Segment number'):
    index_route(tmp_path)


def test_snapshot_comparison_never_claims_fresh_execution_or_missing_identity():
  from openpilot.tools.car_porting.gm_trip_report import compare_snapshots
  item = {'vehicle_identity_hash': None, 'timestamp': '2026-09-14T00:00:00+00:00', 'emissions': {}, 'readings': {}}
  result = compare_snapshots({'before': item, 'after': item})
  assert result['same_vehicle'] is None
  assert result['fresh_monitor_execution'] == 'unproven'
  assert result['phases'][0]['ecm_dtc_statuses']['pending']['state'] == 'not_read'
