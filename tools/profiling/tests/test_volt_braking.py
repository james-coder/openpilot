from copy import deepcopy
from openpilot.tools.profiling.volt_braking import describe_event


def rows():
  return [
    {
      't': k * 0.01 - 4,
      'v': max(0, 2 - k * 0.006),
      'a': -0.6,
      'foot': False,
      'regen': False,
      'valid': True,
      'active': True,
      'radar_valid': True,
      'lead': True,
      'd': 5.0,
      'track': 1,
    }
    for k in range(601)
  ]


def test_manual_input_at_window_start_is_not_autonomous():
  r = rows()
  for sample in r:
    sample['foot'] = True
    sample['active'] = False
  assert describe_event(r)['kind'] == 'manual'


def test_telemetry_coverage_does_not_turn_unknown_into_autonomous():
  r = rows()
  for sample in r[100:300]:
    sample.pop('active')
  assert describe_event(r)['kind'] == 'unknown'
  assert describe_event(rows())['kind'] == 'autonomous'


def test_takeover_is_distinct_from_manual_and_autonomous():
  r = rows()
  for sample in r[200:]:
    sample['foot'] = True
    sample['active'] = False
  assert describe_event(r)['kind'] == 'takeover'


def test_last_contiguous_creep_phase_excludes_prior_low_speed():
  r = rows()
  for sample in r[:100]:
    sample['v'] = 0.5
  for sample in r[100:200]:
    sample['v'] = 3.0
  result = describe_event(r)
  assert 1.9 < result['low_speed_seconds'] < 2.1


def test_settled_gap_rejects_faulted_radar():
  r = deepcopy(rows())
  for sample in r:
    sample['errors'] = ['canError']
  assert describe_event(r)['settled_radar_gap'] is None


def test_contiguous_control_gap_cannot_hide_an_intervention():
  r = rows()
  for sample in r[100:150]:
    sample.pop('active')
  result = describe_event(r)
  assert result['control_coverage'] > 0.8
  assert result['kind'] == 'unknown'


def test_archive_verification_requires_all_files_and_rejects_corruption(tmp_path):
  import hashlib
  import json
  import pytest
  from openpilot.tools.profiling.archive_braking import archive, NAMES

  route = '00000022--0123456789'
  raw = tmp_path / 'raw'
  segment = raw / (route + '--0')
  segment.mkdir(parents=True)
  manifest = []
  for name in NAMES:
    (segment / name).write_bytes(b'fixture')
    manifest.append({'path': segment.name + '/' + name, 'size': 7, 'sha256': hashlib.sha256(b'fixture').hexdigest()})
  source = tmp_path / 'remote.json'
  source.write_text(json.dumps(manifest))
  archive(raw, [route], local_manifest=source)
  assert json.loads((tmp_path / 'archive-manifest.json').read_text())['verified']
  (segment / 'rlog.zst').write_bytes(b'changed')
  with pytest.raises(ValueError, match='Hash mismatch'):
    archive(raw, [route], local_manifest=source)
  source.write_text(json.dumps(manifest[:-1]))
  with pytest.raises(ValueError, match='both road cameras'):
    archive(raw, [route], local_manifest=source)
