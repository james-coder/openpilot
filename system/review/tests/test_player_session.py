import threading
import time

import pytest

from openpilot.system.review.player import session as session_mod
from openpilot.system.review.player.events import events_path_for
from openpilot.system.review.player.media import MediaError
from openpilot.system.review.player.session import DashcamSession
from openpilot.system.review.tests.player_helpers import ROUTE, event_records, frame_red, make_segment, wait_until, write_events

FRAMES = 40        # a 2 s segment
MONO0 = 1000.0


class XattrStore:
  def __init__(self):
    self.flags = set()

  def get(self, path, attr):
    return b'1' if path in self.flags else None

  def set(self, path):
    self.flags.add(path)


class Rig:
  def __init__(self, tmp_path, **kw):
    self.root = tmp_path / 'media' / '0' / 'realdata'
    self.root.mkdir(parents=True)
    self.events = events_path_for(self.root)
    self.xattr = XattrStore()
    self.logged = []
    kw.setdefault('protect_fn', self.xattr.set)
    kw.setdefault('getxattr_fn', self.xattr.get)
    kw.setdefault('logger', self.logged.append)
    kw.setdefault('drop_realtime_fn', lambda: None)
    self.session = DashcamSession(self.root, **kw)

  def segment(self, n, **kw):
    return make_segment(self.root, n, frames=kw.pop('frames', FRAMES), mono0=kw.pop('mono0', MONO0), **kw)

  def log_events(self, *records):
    flat = []
    for r in records:
      flat += r
    write_events(self.events, flat)

  def start(self):
    self.session.start()
    assert wait_until(lambda: self.session.view().events_state != 'loading')
    return self.session

  def pump(self, cond, timeout=8.0, dt=0.0):
    s = self.session

    def check():
      s.update(dt)
      return cond(s.view())
    return wait_until(check, timeout)

  def open_event(self, index=0):
    s = self.start()
    s.select(s.view().rows[index].key)
    assert self.pump(lambda v: v.clip_state in ('ready', 'error')), 'clip never loaded'
    return s

  def ready(self):
    """Open the first event and wait until its first frame has been decoded."""
    s = self.open_event()
    assert self.pump(lambda v: v.clip_state == 'ready' and not v.buffering and v.frame is not None), 'no frame'
    return s


@pytest.fixture
def rig(tmp_path):
  r = Rig(tmp_path)
  yield r
  r.session.close()
  r.session.join(5)


def seg(n):
  return f'{ROUTE}--{n}'


def red_of(frame):
  return float(frame[100:200, 100:200, 0].mean())


# ---- event list --------------------------------------------------------------------------------------------------------------

def test_no_events_file_is_a_friendly_empty_state(rig):
  v = rig.start().view()
  assert v.events_state == 'missing' and v.rows == () and v.clip_state == 'none'


def test_empty_and_corrupt_event_files_do_not_raise(rig):
  rig.events.parent.mkdir(parents=True)
  rig.events.write_bytes(b'\xff\x00garbage\n{broken\n')
  v = rig.start().view()
  assert v.events_state == 'ok' and v.rows == ()


def test_events_carry_footage_state_and_protection(rig):
  rig.segment(4)
  rig.xattr.set(str(rig.root / seg(4)))
  rig.log_events(event_records(seg(4), mono=MONO0 + 1, ident=1), event_records(seg(2), mono=MONO0 - 500, ident=2))
  v = rig.start().view()
  assert [r.id for r in v.rows] == [2, 1]
  by_id = {r.id: v.statuses[r.key] for r in v.rows}
  assert by_id[1].state == 'ready' and by_id[1].protected is True
  assert by_id[2].state == 'deleted'


def test_refresh_picks_up_a_new_event(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1, ident=1))
  s = rig.start()
  assert len(s.view().rows) == 1
  rig.log_events(event_records(seg(4), mono=MONO0 + 1, ident=1), event_records(seg(4), mono=MONO0 + 1.5, ident=2))
  s.refresh()
  assert wait_until(lambda: len(s.view().rows) == 2)


# ---- opening and playing a clip ----------------------------------------------------------------------------------------------

def test_selecting_an_event_opens_paused_on_a_decoded_frame_with_the_event_marked(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  v = rig.ready().view()
  assert v.clip_state == 'ready' and not v.playing and v.duration == pytest.approx(FRAMES / 20)
  assert v.event_time == pytest.approx(1.0)
  assert v.position == 0.0                                  # 5 s of pre-roll does not fit before 1.0 s: clamped to the start
  assert abs(red_of(v.frame) - frame_red(0)) < 8
  assert v.frame_size == (526, 330)
  assert v.telemetry is not None and v.now is not None and v.now['speed'] == pytest.approx(10.0, abs=0.2)


def test_stepping_scrubbing_jumping_and_rates(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.step(1)
  assert rig.pump(lambda v: v.frame_id == (v.frame_id[0], 1) and abs(red_of(v.frame) - frame_red(1)) < 8)
  s.step(5)
  assert rig.pump(lambda v: v.frame_id[1] == 6 and abs(red_of(v.frame) - frame_red(6)) < 8)
  s.step(-2)
  assert rig.pump(lambda v: v.frame_id[1] == 4 and abs(red_of(v.frame) - frame_red(4)) < 8)
  s.scrub(0.5, 'start')
  s.scrub(0.5, 'move')
  s.scrub(0.5, 'end')
  assert rig.pump(lambda v: v.frame_id[1] == 20 and abs(red_of(v.frame) - frame_red(20)) < 8)
  s.jump(-1.0)
  assert rig.pump(lambda v: v.frame_id[1] == 0 and v.position == pytest.approx(0.0) and abs(red_of(v.frame) - frame_red(0)) < 8)
  for rate in (0.1, 0.25, 0.5, 1.0, 2.0):
    s.set_rate(rate)
    assert s.view().rate == rate


def test_scrubbing_holds_playback_and_resumes_it(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.toggle_play()
  assert s.view().playing
  s.scrub(0.25, 'start')
  assert not s.view().playing
  s.scrub(0.75, 'move')
  assert s.view().position == pytest.approx(0.75 * s.view().duration)
  s.scrub(0.75, 'end')
  assert s.view().playing
  s.scrub(0.1, 'end')       # an 'end' with no matching 'start' does nothing
  assert s.view().position == pytest.approx(0.75 * s.view().duration, abs=0.2)


def test_a_cancelled_touch_ends_the_scrub_without_seeking(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.scrub(0.5, 'start')
  pos = s.view().position
  s.scrub(0.0, 'cancel')
  assert s.view().position == pos and not s.view().playing
  s.scrub(0.9, 'move')       # no longer scrubbing
  assert s.view().position == pos


def test_playing_runs_to_the_end_decoding_every_frame_on_demand(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.set_rate(2.0)
  s.toggle_play()
  seen = []
  assert rig.pump(lambda v: (seen.append(v.frame_id[1]) or v.ended), dt=0.05)
  v = s.view()
  assert v.ended and not v.playing
  assert seen == sorted(seen) and seen[-1] == FRAMES - 1
  assert abs(red_of(v.frame) - frame_red(FRAMES - 1)) < 8
  s.toggle_play()      # play from the end restarts
  assert s.view().playing and s.view().position == 0.0


def test_event_near_a_segment_boundary_pulls_in_the_neighbours(rig):
  rig.segment(3, mono0=MONO0 - 2.0)
  rig.segment(4)
  rig.segment(5, mono0=MONO0 + 2.0)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.open_event()
  parts = s._clip.clip.parts
  assert [p.segment for p in parts] == [seg(3), seg(4), seg(5)]
  v = s.view()
  assert v.duration == pytest.approx(3 * FRAMES / 20)
  assert v.event_time == pytest.approx(2.0 + 1.0)
  assert v.position == 0.0
  s.step(FRAMES + 3)       # into the middle segment
  assert rig.pump(lambda v: v.frame_id[1] == FRAMES + 3 and abs(red_of(v.frame) - frame_red(3)) < 8)


def test_a_missing_neighbour_is_just_left_out(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.open_event()
  assert [p.segment for p in s._clip.clip.parts] == [seg(4)]


def test_event_far_from_the_boundaries_uses_only_its_own_segment(rig):
  rig.segment(4, frames=1000, gop=20, mono0=MONO0)      # 50 s
  rig.segment(3, mono0=MONO0 - 2.0)
  rig.log_events(event_records(seg(4), mono=MONO0 + 25.0))
  s = rig.open_event()
  assert [p.segment for p in s._clip.clip.parts] == [seg(4)]
  assert s.view().position == pytest.approx(20.0)         # pre-roll of 5 s


def test_a_segment_with_no_qlog_still_plays_without_a_marker_or_telemetry(rig):
  rig.segment(4, qlog=False)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  v = rig.ready().view()
  assert v.event_time is None and v.telemetry is None and v.now is None and v.position == 0.0


def test_a_corrupt_qlog_degrades_to_no_marker(rig):
  d = rig.segment(4)
  (d / 'qlog.zst').write_bytes(b'not zstd at all' * 20)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  v = rig.ready().view()
  assert v.clip_state == 'ready' and v.event_time is None


# ---- footage that cannot be played ---------------------------------------------------------------------------------------------

def error_for(rig, setup):
  setup()
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  v = rig.open_event().view()
  assert v.clip_state == 'error', v
  return v.message


def test_deleted_footage_says_so(rig):
  assert 'deleted' in error_for(rig, lambda: None)


def test_footage_still_being_recorded_is_not_touched(rig):
  def setup():
    (rig.segment(4) / 'qlog.lock').write_bytes(b'')
  assert 'still being recorded' in error_for(rig, setup)


def test_zero_length_video_is_reported(rig):
  def setup():
    (rig.segment(4) / 'qcamera.ts').write_bytes(b'')
  assert 'No video' in error_for(rig, setup)


def test_corrupt_video_is_reported_not_raised(rig):
  def setup():
    (rig.segment(4) / 'qcamera.ts').write_bytes(b'\x00\x01garbage' * 300)
  assert 'Video unavailable' in error_for(rig, setup)


def test_an_event_without_a_segment(rig):
  rig.log_events([dict(phase='start', id=1, mono=5.0, wall=1_790_000_000.0, kinds=['hard_brake'], peaks={'decel_g': 0.5})])
  v = rig.open_event().view()
  assert v.clip_state == 'error' and 'No segment' in v.message and not v.can_protect


# ---- protect -----------------------------------------------------------------------------------------------------------------

def test_protect_sets_the_flag_through_the_injected_call_and_reports_it(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  assert s.view().can_protect and s.view().segment_status.protected is False
  s.protect()
  assert rig.pump(lambda v: v.segment_status.protected is True)
  assert str(rig.root / seg(4)) in rig.xattr.flags
  assert rig.pump(lambda v: v.notice.startswith('Protected'))


def test_protect_failure_is_reported_and_logged(rig):
  def refuse(path):
    raise PermissionError('read-only filesystem')
  rig.session._protect_fn = refuse
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.protect()
  assert rig.pump(lambda v: 'Could not protect' in v.notice)
  assert 'PermissionError' in s.view().notice and s.view().segment_status.protected is False
  assert any('could not protect' in m for m in rig.logged)


def test_protect_that_does_not_stick_is_not_reported_as_success(rig):
  rig.session._protect_fn = lambda path: None       # "succeeds" but the flag never appears
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.protect()
  assert rig.pump(lambda v: 'Could not protect' in v.notice)


def test_protect_on_deleted_footage_does_nothing(rig):
  calls = []
  rig.session._protect_fn = calls.append
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.open_event()
  assert not s.view().can_protect
  s.protect()
  time.sleep(0.2)
  assert calls == []


# ---- safety: worker, heat, close, memory --------------------------------------------------------------------------------------

def test_the_worker_drops_real_time_scheduling_on_its_own_thread_before_decoding(tmp_path):
  order = []

  def drop():
    order.append(('drop', threading.get_ident()))
  r = Rig(tmp_path, drop_realtime_fn=drop)
  r.segment(4)
  r.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  try:
    s = r.ready()
    assert order and order[0][1] == s.stats['worker_thread'] != threading.get_ident()
    assert len(order) == 1 and s.stats['decoded'] > 0
  finally:
    r.session.close()


def test_a_failure_to_drop_real_time_is_logged_and_does_not_stop_the_worker(tmp_path):
  def boom():
    raise PermissionError('sched_setscheduler denied')
  r = Rig(tmp_path, drop_realtime_fn=boom)
  r.segment(4)
  r.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  try:
    r.ready()
    assert any('real-time' in m for m in r.logged)
  finally:
    r.session.close()


def test_a_hot_device_pauses_playback_and_stops_decoding(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.toggle_play()
  assert s.view().playing
  s.update(0.0, hot=True)
  v = s.view()
  assert not v.playing and v.hot and 'hot' in v.notice
  s.toggle_play()
  assert not s.view().playing       # cannot restart while hot
  time.sleep(0.1)                   # a slice already under way may finish
  before = s.stats['decoded']
  s.step(10)
  s.update(0.0, hot=True)
  time.sleep(0.3)
  assert s.stats['decoded'] == before
  s.update(0.0, hot=False)
  assert rig.pump(lambda v: v.frame_id[1] == 10 and abs(red_of(v.frame) - frame_red(10)) < 8)


def test_close_stops_the_worker_and_further_calls_are_harmless(rig):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.close()
  assert s.join(5)
  n = s.stats['decoded']
  s.update(0.1)
  s.step(1)
  s.toggle_play()
  s.scrub(0.5, 'start')
  s.protect()
  s.select(None)
  v = s.view()
  time.sleep(0.1)
  assert s.stats['decoded'] == n and v.clip_state == 'none'
  s.close()


def test_the_frame_cache_stays_under_its_cap_while_playing_a_clip_larger_than_it(tmp_path):
  one = 526 * 330 * 3
  r = Rig(tmp_path, max_cache_bytes=6 * one)
  r.segment(4, frames=80)
  r.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  try:
    s = r.ready()
    s.set_rate(2.0)
    s.toggle_play()
    peak = 0

    def done(v):
      nonlocal peak
      peak = max(peak, s._clip.cache.nbytes)
      return v.ended
    assert r.pump(done, dt=0.05, timeout=15)
    assert 0 < peak <= 6 * one
  finally:
    r.session.close()


def test_selecting_a_new_event_frees_the_old_clip_and_ignores_the_stale_load(rig):
  rig.segment(4)
  rig.segment(6, mono0=MONO0 + 100)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0, ident=1), event_records(seg(6), mono=MONO0 + 101.0, ident=2))
  s = rig.ready()
  old = s._clip.cache
  assert len(old) > 0
  first, second = s.view().rows
  s.select(first.key)
  s.select(second.key)
  assert len(old) == 0 and old.nbytes == 0
  assert rig.pump(lambda v: v.clip_state == 'ready' and v.selected == second.key and v.frame is not None and not v.buffering)
  assert s._clip.row.id == second.id and s._clip.gen == s._gen


def test_a_damaged_stretch_becomes_a_gap_that_playback_skips(rig, monkeypatch):
  real = session_mod.read_video

  def damaged(index, first):
    for n, item in enumerate(real(index, first)):
      if n >= 15:
        raise MediaError('video is damaged (InvalidDataError)')
      yield item
  monkeypatch.setattr(session_mod, 'read_video', damaged)
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  s.set_rate(2.0)
  s.toggle_play()
  states = []
  assert rig.pump(lambda v: (states.append((v.in_gap, v.message)) or v.ended), dt=0.05, timeout=15)
  assert any(in_gap for in_gap, _ in states)
  assert any('damaged' in m for _, m in states)
  assert s._clip.gaps and s._clip.gaps[0][0] == 15


def test_a_decoder_that_stops_answering_pauses_playback_by_itself(rig, monkeypatch):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  s = rig.ready()
  monkeypatch.setattr(s, '_decode_slice', lambda: False)    # worker alive but no longer decoding
  time.sleep(0.1)                                           # let a slice already under way finish
  s._clip.cache.clear()
  s.step(15)
  s.toggle_play()
  assert s.view().playing
  for _ in range(60):
    s.update(0.25)
  v = s.view()
  assert not v.playing and 'not responding' in v.notice and v.buffering


def test_a_failed_step_becomes_a_visible_error_and_the_worker_keeps_serving(rig, monkeypatch):
  calls = {'n': 0}
  real = rig.session._load_events

  def flaky():
    calls['n'] += 1
    if calls['n'] == 1:
      raise RuntimeError('boom')
    return real()
  monkeypatch.setattr(rig.session, '_load_events', flaky)
  rig.session.start()
  assert wait_until(lambda: rig.session.view().events_state == 'unreadable')
  assert rig.logged and rig.session.stats['worker_errors'] == 1
  rig.session.refresh()
  assert wait_until(lambda: rig.session.view().events_state == 'missing')
  assert rig.session.worker_alive


def test_a_clip_load_that_blows_up_shows_an_error_instead_of_hanging(rig, monkeypatch):
  rig.segment(4)
  rig.log_events(event_records(seg(4), mono=MONO0 + 1.0))
  monkeypatch.setattr(rig.session, '_load_part', lambda segment: (_ for _ in ()).throw(RuntimeError('unexpected')))
  v = rig.open_event().view()
  assert v.clip_state == 'error' and 'unexpected error' in v.message and rig.session.worker_alive


def test_a_decode_loop_that_keeps_failing_is_reported_dead_and_the_view_says_so(rig, monkeypatch):
  monkeypatch.setattr(session_mod, 'WORKER_FAILURE_LIMIT', 3)
  monkeypatch.setattr(rig.session, '_decode_slice', lambda: (_ for _ in ()).throw(RuntimeError('always')))
  monkeypatch.setattr(rig.session._stop, 'wait', lambda t=None: False)
  rig.session.start()
  assert wait_until(lambda: not rig.session.worker_alive)
  v = rig.session.view()
  assert v.clip_state == 'error' and 'Playback' in v.message and not v.worker_alive


def test_the_view_is_always_buildable(rig):
  s = rig.session
  assert s.view().clip_state == 'none'
  s.update(0.1)
  s.step(1)
  s.protect()
  s.select('nonexistent')
  assert s.view().clip_state == 'none'
