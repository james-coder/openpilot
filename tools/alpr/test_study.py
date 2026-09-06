import pytest

from openpilot.tools.alpr.evaluate import evaluate, normalize, split_for_route
from openpilot.tools.alpr.export import choose_segments


def test_selection_spans_range_and_keeps_route_order():
  rows = [{'segment': f'00000001--0123456789--{n}'} for n in [20, 1, 10, 2, 3]]
  assert [r['segment'].rsplit('--', 1)[1] for r in choose_segments(rows, 3)] == ['1', '3', '20']
  assert len(choose_segments(rows, 30)) == 5
  with pytest.raises(ValueError):
    choose_segments(rows, 0)


def test_route_split_does_not_leak_adjacent_segments():
  assert split_for_route('00000001--0123456789--0') == split_for_route('00000001--0123456789--50')
  assert normalize('ab-01 cd') == 'AB01CD'
  assert normalize('O0I1') == 'O0I1'


def test_false_accepts_abstentions_and_misses_are_counted():
  segment = '00000001--0123456789--0'
  plate = {'box': [0, 0, 100, 40], 'text': 'ABC123', 'readable': True, 'encounter': 'vehicle1'}
  frames = [{'segment': segment, 'camera': 'fcamera', 'frame': 0, 'reviewed': True, 'plates': [plate]},
            {'segment': segment, 'camera': 'fcamera', 'frame': 20, 'reviewed': True, 'plates': [plate]},
            {'segment': segment, 'camera': 'fcamera', 'frame': 40, 'reviewed': False, 'plates': []}]
  rows = [{'segment': segment, 'camera': 'fcamera', 'frame': 0, 'box': [0, 0, 100, 40], 'text': 'ABO123', 'encounter': 'track1'},
          {'segment': segment, 'camera': 'fcamera', 'frame': 0, 'box': [200, 200, 300, 240], 'text': 'NOISE', 'encounter': 'track2'},
          {'segment': segment, 'camera': 'fcamera', 'frame': 40, 'box': [0, 0, 100, 40], 'text': 'ABC123', 'encounter': 'unreviewed'}]
  tracks = [{'encounter': 'track1', 'text': 'ABO123', 'accepted': True},
            {'encounter': 'track2', 'text': 'NOISE', 'accepted': False},
            {'encounter': 'unreviewed', 'text': 'ABC123', 'accepted': True}]
  result = evaluate({'frames': frames}, rows, tracks, 'all')
  assert result['reviewed_frames'] == 2
  assert result['frame_metrics']['all']['detection_recall']['rate'] == .5
  assert result['false_detections_on_reviewed_frames'] == 1
  assert result['false_accepted_tracks']['rate'] == 1
  assert result['track_abstention']['rate'] == .5
  assert result['encounter_exact']['rate'] == 0
  assert result['independent_readable_encounters'] == 1


def test_no_labels_means_unknown_accuracy():
  result = evaluate({'frames': []}, [], [])
  assert result['encounter_exact']['rate'] is None
  assert not result['target_200_met']
