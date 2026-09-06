from openpilot.tools.alpr.assisted_selection import radar_at, associate_radar, illumination, score_sample


def test_radar_never_uses_future_stale_or_unhealthy_messages():
  points = [{'id': 1, 'range_m': 5., 'left_m': 0.}]
  rows = [{'mono_ns': 1_000_000_000, 'healthy': True, 'points': points},
          {'mono_ns': 1_100_000_000, 'healthy': False, 'points': points}]
  times = [r['mono_ns'] for r in rows]
  assert radar_at(rows, times, 999_000_000) is None
  assert radar_at(rows, times, 1_050_000_000)['age_ms'] == 50
  assert radar_at(rows, times, 1_100_000_000) is None
  assert radar_at(rows[:1], times[:1], 1_200_000_000) is None
  assert radar_at(rows, times, None) is None


def test_radar_alignment_does_not_assign_adjacent_target_to_central_plate():
  radar = {'age_ms': 2, 'points': [{'id': 1, 'range_m': 5., 'left_m': 3.5}]}
  assert associate_radar(radar, [920, 600, 1008, 630], 'fcamera') is None
  radar['points'].append({'id': 2, 'range_m': 5., 'left_m': 0.})
  assert associate_radar(radar, [920, 600, 1008, 630], 'fcamera')['id'] == 2


def test_lighting_prefill_exposes_heuristic_and_missing_time():
  assert illumination(None, 100)['value'] == 'day'
  assert illumination(None, 10)['value'] == 'night'
  assert illumination(None, 100)['local_time'] is None
  assert 'estimate' in illumination(None, 100)['source']


def test_quality_priority_does_not_use_ocr_confidence():
  a = {'width': 100, 'sharpness': 100, 'detection_confidence': .9, 'ocr_confidence': .1}
  b = {**a, 'width': 50, 'ocr_confidence': 1.}
  assert score_sample(a) > score_sample(b)


def test_additive_batch_preserves_original_encounters_and_rejects_collisions():
  import copy
  import pytest
  from openpilot.tools.alpr.append_assisted import extend_queue
  old = {'id': 'original', 'tier': 'close', 'observation_count': 8, 'samples': [{'id': 'saved-sample', 'box': [1, 2, 3, 4]}]}
  new = {'id': 'batch-new', 'tier': 'large', 'observation_count': 9, 'samples': []}
  original = {'version': 1, 'dataset_id': 'assisted-v1', 'encounters': [old], 'stats': {}}
  snapshot = copy.deepcopy(original)
  source = {'encounters': [new]}
  result = extend_queue(original, source, {'append_ids': ['batch-new'], 'recommended_ids': ['original', 'batch-new']})
  assert original == snapshot
  assert result['encounters'][0] == old
  assert result['stats']['encounters'] == 2
  for selection in [
    {'append_ids': ['original'], 'recommended_ids': []},
    {'append_ids': ['batch-new', 'batch-new'], 'recommended_ids': []},
    {'append_ids': [], 'recommended_ids': ['missing']},
  ]:
    with pytest.raises(ValueError):
      extend_queue(original, source, selection)
