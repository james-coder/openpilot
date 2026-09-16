import importlib.util
import json
from pathlib import Path

import pytest
import zstandard

SPEC = importlib.util.spec_from_file_location('report', Path(__file__).with_name('modem_validation_report.py'))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def test_sustained_gps_gap_and_missing():
  rows = [{'t': t, 'fix': True, 'accuracy': 2} for t in range(121)]
  assert r.summarize(rows)['gps_sustained']['status'] == 'pass'
  assert r.summarize(rows[:40] + rows[50:])['gps_sustained']['status'] == 'not_observed'
  assert r.summarize([])['gps_sustained']['status'] == 'not_observed'
  rows[50]['fix'] = False
  assert r.summarize(rows)['gps_sustained']['status'] == 'not_observed'


def test_cached_model_is_not_fingerprinting():
  rows = [{'t': 0, 'loaded_car_params': {'brand': 'gm', 'fingerprint': 'VOLT'}, 'car_state': True, 'started': True}]
  assert r.summarize(rows)['vehicle_identification']['status'] == 'not_observed'
  rows.insert(0, {'t': 0, 'car': {'brand': 'gm', 'fingerprint': 'CHEVROLET VOLT PREMIER 2017'}})
  assert r.summarize(rows)['vehicle_identification']['status'] == 'pass'
  rows[0]['car']['brand'] = 'mock'
  assert r.summarize(rows)['vehicle_identification']['status'] == 'fail'


def test_health_errors_and_corrupt_input():
  rows = [{'t': 0, 'missing_expected_count': 1, 'panda_fault_count': 1, 'engaged': True}]
  out = r.summarize(rows, complete=False)
  assert out['expected_processes']['status'] == 'fail'
  assert out['panda_faults']['status'] == 'fail'
  assert out['ordinary_engagement']['status'] == 'not_observed'
  assert out['input_integrity']['status'] == 'fail'


def test_stale_live_data_and_wrong_kernel(tmp_path):
  p = tmp_path / 'live.jsonl'
  rows = [
    {'metadata': {'kernel': 'old', 'boot': 'b', 'revision': 'r'}},
    {
      'elapsed_seconds': 0,
      'fresh': {'gpsLocationExternal': False},
      'gps': {'has_fix': True, 'horizontal_accuracy_m': 1},
      'modem': {'available': True, 'connected': True, 'age_seconds': 10},
    },
  ]
  p.write_text(''.join(json.dumps(d) + '\n' for d in rows))
  meta, normalized = r.live_rows(p)
  assert 'fix' not in normalized[0] and 'connected' not in normalized[0]
  assert not r.build_report([], [p])['groups']
  p.write_text('{')
  assert r.build_report([], [p])['sources'][0]['status'] == 'fail'


def test_bounded_and_multiframe_zstd(tmp_path, monkeypatch):
  p = tmp_path / 'qlog.zst'
  z = zstandard.ZstdCompressor()
  p.write_bytes(z.compress(b'a' * 100) + z.compress(b'b' * 100))
  assert r.bounded_bytes(p) == b'a' * 100 + b'b' * 100
  p.write_bytes(z.compress(b'a' * 100)[:-1])
  with pytest.raises(ValueError):
    r.bounded_bytes(p)
  p.write_bytes(z.compress(b'a' * 1000))
  monkeypatch.setattr(r, 'MAX_BYTES', 100)
  with pytest.raises(ValueError):
    r.bounded_bytes(p)


def test_boot_boundaries_and_repeated_metadata(tmp_path):
  paths = []
  for boot in ('one', 'two'):
    p = tmp_path / (boot + '.jsonl')
    rows = [{'metadata': {'kernel': r.BUILD, 'boot': boot, 'revision': 'r'}}]
    rows.extend({'elapsed_seconds': t, 'fresh': {'gpsLocationExternal': True},
                 'gps': {'has_fix': True, 'horizontal_accuracy_m': 1}} for t in range(70))
    p.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    paths.append(p)
  report = r.build_report([], paths)
  assert len(report['groups']) == 2
  assert all(g['checks']['gps_sustained']['status'] == 'not_observed' for g in report['groups'])
  paths[0].write_text(paths[0].read_text() + json.dumps(rows[0]) + '\n')
  assert r.build_report([], paths)['sources'][0]['status'] == 'fail'


def test_capnp_log_privacy(tmp_path):
  from cereal import log

  p = tmp_path / 'qlog'
  init = log.Event.new_message()
  d = init.init('initData')
  d.kernelVersion = r.BUILD
  d.gitCommit = 'abc'
  d.bootlogId = 'boot'
  d.dongleId = 'PRIVATE'
  gps = log.Event.new_message()
  gps.valid = True
  gps.logMonoTime = 1000000000
  g = gps.init('gpsLocationExternal')
  g.hasFix = True
  g.latitude = 42
  g.longitude = 84
  g.horizontalAccuracy = 2
  p.write_bytes(init.to_bytes() + gps.to_bytes())
  report = r.build_report([p], [])
  assert report['groups'][0]['checks']['gps_sustained']['samples'] == 1
  assert 'PRIVATE' not in json.dumps(report) and 'latitude' not in json.dumps(report)
  d.bootlogId = ''
  entries = d.params.init('entries', 1)
  entries[0].key = 'CurrentBootlog'
  entries[0].value = b'fallback-boot'
  init.clear_write_flag()
  gps.clear_write_flag()
  p.write_bytes(init.to_bytes() + gps.to_bytes())
  meta, _ = r.route_rows(p)
  assert meta['boot'] == 'fallback-boot'
