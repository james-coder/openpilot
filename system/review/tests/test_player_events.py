import json
import os
from datetime import UTC, datetime

import pytest

from openpilot.system.review.player import events as ev
from openpilot.system.review.player.events import (
  SegmentStatus, describe_time, events_path_for, kinds_label, neighbour_segment, parse_events, peaks_label, read_events, segment_status,
  status_label, valid_segment_name,
)
from openpilot.system.review.tests.player_helpers import ROUTE, event_records, write_events

SEG = f'{ROUTE}--4'


def lines(*records):
  return [r if isinstance(r, str | bytes) else json.dumps(r) for r in records]


def rec(phase, ident=1, **kw):
  base = dict(v=1, id=ident, phase=phase, mono=500.0, wall=1_790_000_000.0, kinds=['hard_brake'], peaks={'decel_g': 0.5}, segment=SEG)
  base.update(kw)
  return base


# ---- parsing ------------------------------------------------------------------------------------------------------------------

def test_start_update_end_make_one_episode_with_merged_kinds_and_peak_maxima():
  rows = parse_events(lines(rec('start'), rec('update', kinds=['hard_brake', 'abs_stop'], peaks={'decel_g': 0.7, 'horizontal_g': 0.2}),
                            rec('end', mono=503.0, wall=1_790_000_004.0, peaks={'decel_g': 0.7}, segment=f'{ROUTE}--5')))
  assert len(rows) == 1
  r = rows[0]
  assert r.kinds == ('hard_brake', 'abs_stop')
  assert r.peaks == {'decel_g': 0.7, 'horizontal_g': 0.2}
  assert r.mono == 500.0 and r.wall == 1_790_000_000.0          # the first trigger, not the closing record
  assert r.segment == SEG and r.segments == (SEG, f'{ROUTE}--5')
  assert r.complete


def test_newest_first_in_file_order_even_when_the_wall_clock_runs_backwards():
  rows = parse_events(lines(rec('start', 1, mono=100.0, wall=1_790_000_900.0), rec('end', 1), rec('start', 2, mono=200.0, wall=1_700_000_000.0)))
  assert [r.mono for r in rows] == [200.0, 100.0]


def test_a_new_start_with_a_reused_id_after_reboot_is_a_new_episode():
  rows = parse_events(lines(rec('start', 1, mono=100.0), rec('end', 1, mono=100.0), rec('start', 1, mono=50.0)))
  assert len(rows) == 2 and len({r.key for r in rows}) == 2


def test_orphan_update_or_end_without_a_start_still_shows_up():
  rows = parse_events(lines(rec('update', 7, mono=10.0), rec('end', 8, mono=20.0)))
  assert [r.id for r in rows] == [8, 7]


def test_garbage_lines_are_skipped_not_raised():
  junk = lines('', '   ', 'not json', '{"phase": "start"', '[1, 2]', '"text"', '42', 'null', b'\xff\xfe\x00garbage', b'{"phase": "bogus"}',
               '{"phase": 5}', '{' * 5000, 'x' * 100_000, rec('start'))
  rows = parse_events(junk)
  assert len(rows) == 1 and rows[0].kinds == ('hard_brake',)


def test_missing_and_mistyped_fields_degrade_to_none_and_empty():
  rows = parse_events(lines({'phase': 'start'}, {'phase': 'start', 'id': 'x', 'mono': 'soon', 'wall': True, 'kinds': 'hard_brake',
                                                 'peaks': [1], 'segment': 7}))
  assert len(rows) == 2
  for r in rows:
    assert r.mono is None and r.wall is None and r.kinds == () and r.peaks == {} and r.segment is None and r.id is None


def test_non_finite_numbers_are_dropped():
  # python's json accepts these bare words; they must not reach the formatter
  rows = parse_events(['{"phase": "start", "id": 1, "wall": NaN, "mono": Infinity, "peaks": {"decel_g": -Infinity, "x": 0.4}}'])
  assert rows[0].wall is None and rows[0].mono is None and rows[0].peaks == {'x': 0.4}


def test_unsafe_segment_names_are_rejected():
  for bad in ('../etc--1', '/abs--1', 'a/b--1', '..--1', 'noseg', f'{ROUTE}--x', f'{ROUTE}--', '.hidden--1', 'a\x00b--1', ''):
    assert not valid_segment_name(bad), bad
  assert valid_segment_name(SEG)
  rows = parse_events(lines(rec('start', segment='../../data--1')))
  assert rows[0].segment is None


def test_kinds_and_peaks_are_bounded():
  rows = parse_events(lines(rec('start', kinds=[f'k{i}' for i in range(50)] + [3, None], peaks={f'p{i}': 1.0 for i in range(50)})))
  assert len(rows[0].kinds) <= ev.MAX_KINDS and len(rows[0].peaks) <= ev.MAX_PEAKS


def test_event_cap_keeps_the_newest():
  recs = []
  for i in range(30):
    recs += [rec('start', i, mono=float(i)), rec('end', i, mono=float(i))]
  rows = parse_events(lines(*recs), max_events=10)
  assert len(rows) == 10 and rows[0].id == 29


def test_keys_are_stable_when_the_file_grows():
  a = parse_events(lines(rec('start', 1, mono=100.0), rec('end', 1)))
  b = parse_events(lines(rec('start', 1, mono=100.0), rec('end', 1), rec('start', 2, mono=200.0)))
  assert a[0].key == b[1].key


# ---- reading the file --------------------------------------------------------------------------------------------------------

def test_missing_file_is_a_friendly_empty_state(tmp_path):
  assert read_events(tmp_path / 'review' / 'events.jsonl') == ([], 'missing')


def test_empty_file_is_ok_and_empty(tmp_path):
  p = tmp_path / 'events.jsonl'
  p.write_bytes(b'')
  assert read_events(p) == ([], 'ok')


def test_a_directory_or_unreadable_file_is_reported_not_raised(tmp_path):
  assert read_events(tmp_path)[1] == 'unreadable'
  p = tmp_path / 'events.jsonl'
  p.write_text('{}')
  p.chmod(0)
  try:
    rows, state = read_events(p)
    assert rows == [] and state in ('unreadable', 'ok')
  finally:
    p.chmod(0o600)


def test_only_the_tail_is_read_and_a_cut_first_line_is_dropped(tmp_path):
  p = tmp_path / 'events.jsonl'
  recs = []
  for i in range(300):
    recs += [rec('start', i, mono=float(i)), rec('end', i, mono=float(i))]
  write_events(p, recs)
  size = p.stat().st_size
  rows, state = read_events(p, tail_bytes=size // 4, max_events=1000)
  assert state == 'ok' and 0 < len(rows) < 300
  assert rows[0].id == 299
  assert all(r.mono is not None for r in rows)


def test_truncated_final_line_is_ignored(tmp_path):
  p = tmp_path / 'events.jsonl'
  write_events(p, [rec('start', 1, mono=1.0), rec('end', 1, mono=1.0)])
  with open(p, 'a') as f:
    f.write('{"phase": "start", "id": 2, "mo')
  rows, _ = read_events(p)
  assert [r.id for r in rows] == [1]


def test_events_path_is_the_review_dir_next_to_realdata():
  assert events_path_for('/data/media/0/realdata/') == ev.Path('/data/media/0/review/events.jsonl')


# ---- presentation ------------------------------------------------------------------------------------------------------------

def test_describe_time_shows_the_stored_time_and_flags_a_clock_that_looks_wrong():
  good = datetime(2026, 10, 3, 14, 22, 7, tzinfo=UTC).timestamp()
  assert describe_time(good, now=good + 60, tz=UTC) == '2026-10-03 14:22:07'
  assert describe_time(good + 30 * 86400, now=good, tz=UTC).endswith('(clock ahead?)')
  assert describe_time(datetime(1970, 1, 2, tzinfo=UTC).timestamp(), tz=UTC).endswith('(clock unset?)')
  assert describe_time(datetime(2024, 6, 1, tzinfo=UTC).timestamp(), tz=UTC).endswith('(clock unset?)')


@pytest.mark.parametrize('bad', [None, 0, -5, float('nan'), float('inf'), 1e30, 'yesterday', True])
def test_describe_time_never_raises_and_says_unknown(bad):
  assert describe_time(bad) == 'Time unknown'


def test_labels():
  assert kinds_label(('hard_brake', 'abs_stop')) == 'Hard brake, ABS stop'
  assert kinds_label(('weird_kind',)) == 'Weird kind'
  assert kinds_label(()) == 'Event'
  assert peaks_label({'horizontal_g': 0.951, 'decel_g': 0.62}) == 'decel 0.62 g  horizontal 0.95 g'
  assert peaks_label({}) == ''
  assert neighbour_segment(SEG, -1) == f'{ROUTE}--3' and neighbour_segment(f'{ROUTE}--0', -1) is None and neighbour_segment('bad', 1) is None


# ---- footage state -----------------------------------------------------------------------------------------------------------

def seg_dir(root, n=4, video=True, lock=False):
  d = root / f'{ROUTE}--{n}'
  d.mkdir()
  if video:
    (d / 'qcamera.ts').write_bytes(b'x' * 100)
  if lock:
    (d / 'qlog.lock').write_bytes(b'')
  return d


def test_segment_status_ready_and_protected_flags(tmp_path):
  d = seg_dir(tmp_path)
  assert segment_status(tmp_path, SEG, lambda p, a: b'1') == SegmentStatus('ready', True)
  assert segment_status(tmp_path, SEG, lambda p, a: None) == SegmentStatus('ready', False)
  seen = []
  segment_status(tmp_path, SEG, lambda p, a: seen.append((p, a)))
  assert seen == [(str(d), 'user.preserve')]


def test_segment_status_unknown_protection_when_xattr_fails(tmp_path):
  seg_dir(tmp_path)

  def boom(p, a):
    raise OSError('no xattr support')
  assert segment_status(tmp_path, SEG, boom) == SegmentStatus('ready', None)


def test_segment_status_deleted_recording_novideo_invalid(tmp_path):
  assert segment_status(tmp_path, SEG, lambda p, a: None) == SegmentStatus('deleted', False)
  seg_dir(tmp_path, 1, video=False)
  assert segment_status(tmp_path, f'{ROUTE}--1', lambda p, a: None).state == 'no_video'
  seg_dir(tmp_path, 2, lock=True)
  assert segment_status(tmp_path, f'{ROUTE}--2', lambda p, a: None).state == 'recording'
  d = seg_dir(tmp_path, 3)
  (d / 'qcamera.ts').write_bytes(b'')
  assert segment_status(tmp_path, f'{ROUTE}--3', lambda p, a: None).state == 'no_video'      # zero-length
  assert segment_status(tmp_path, None).state == 'no_segment'
  assert segment_status(tmp_path, '../escape--1').state == 'no_segment'
  (tmp_path / 'afile--1').write_text('x')
  assert segment_status(tmp_path, 'afile--1', lambda p, a: None).state == 'no_video'          # not a directory


def test_status_label_wording():
  assert status_label(SegmentStatus('ready', True)) == 'Video available, protected'
  assert status_label(SegmentStatus('ready', False)) == 'Video available, not protected'
  assert status_label(SegmentStatus('deleted', False)) == 'Footage deleted'
  assert status_label(None) == 'Checking footage'
  assert status_label(SegmentStatus('recording', None)) == 'Still being recorded'


def test_event_records_helper_round_trips(tmp_path):
  write_events(tmp_path / 'e.jsonl', event_records(SEG, mono=900.0))
  rows, state = read_events(tmp_path / 'e.jsonl')
  assert state == 'ok' and rows[0].segment == SEG and rows[0].peaks == {'decel_g': 0.62}
  assert os.path.exists(tmp_path / 'e.jsonl')
