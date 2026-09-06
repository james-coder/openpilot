import json
import subprocess

import pytest

from openpilot.tools.alpr import export
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


def test_export_stops_transfer_when_ignition_changes(tmp_path, mocker):
  rows = [{'segment': '00000001--0123456789--0', 'files': {'fcamera.hevc': 100}}]
  mocker.patch('sys.argv', ['export', '--output', str(tmp_path)])
  remote = mocker.patch.object(export.subprocess, 'check_output', side_effect=[
    json.dumps(rows), '', subprocess.CalledProcessError(1, 'offroad guard')])
  process = mocker.patch.object(export.subprocess, 'Popen').return_value.__enter__.return_value
  process.wait.side_effect = [subprocess.TimeoutExpired('rsync', 5), -15]
  with pytest.raises(subprocess.CalledProcessError):
    export.main()
  process.terminate.assert_called_once()
  assert remote.call_count == 3
  assert json.loads((tmp_path / 'manifest.json').read_text())['verified'] == {}


def test_network_retry_requires_fresh_offroad_confirmation(tmp_path, mocker):
  rows = [{'segment': '00000001--0123456789--0', 'files': {'fcamera.hevc': 100}}]
  mocker.patch('sys.argv', ['export', '--output', str(tmp_path)])
  mocker.patch.object(export.time, 'sleep')
  remote = mocker.patch.object(export.subprocess, 'check_output', side_effect=[
    json.dumps(rows), subprocess.CalledProcessError(255, 'network'), subprocess.CalledProcessError(1, 'offroad guard')])
  process = mocker.patch.object(export.subprocess, 'Popen')
  with pytest.raises(subprocess.CalledProcessError):
    export.main()
  assert remote.call_count == 3
  process.assert_not_called()
