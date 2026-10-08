import numpy as np
import pytest

from openpilot.system.review.player.media import QlogSummary, VideoIndex
from openpilot.system.review.player.timeline import (
  CONTEXT_S, JUMP_S, MAX_TICK_DT, PRE_ROLL_S, RATES, Clip, Part, PlaybackClock, format_clock, needs_neighbours, scrub_fraction, scrub_x, snap_rate,
)

FPS = 20.0


def fake_index(n: int, fps: float = FPS) -> VideoIndex:
  times = np.arange(n) / fps
  pts = tuple(range(n))
  return VideoIndex('fake', times, 1 / fps, 526, 330, pts, tuple(i % 10 == 0 for i in range(n)), {p: p for p in pts})


def fake_qlog(n: int, mono0: float, car=None) -> QlogSummary:
  mono = mono0 + np.arange(n) / FPS
  car = car if car is not None else np.empty((0, 5))
  c = np.asarray(car, dtype=float).reshape(-1, 5)
  return QlogSummary(mono, c[:, 0], c[:, 1], c[:, 2] > 0.5, c[:, 3] > 0.5, c[:, 4] > 0.5)


def clock(n=100, pos=0.0) -> PlaybackClock:
  times = np.arange(n) / FPS
  return PlaybackClock(times, n / FPS, pos)


# ---- playback clock ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('rate', RATES)
def test_playing_advances_at_the_chosen_rate(rate):
  c = clock()
  c.set_rate(rate)
  c.play()
  for _ in range(20):
    c.tick(0.05)
  assert c.pos == pytest.approx(20 * 0.05 * rate)
  assert c.playing and not c.ended


@pytest.mark.parametrize('rate', RATES)
def test_stepping_moves_exactly_one_frame_at_any_rate_and_pauses(rate):
  c = clock(pos=1.0)
  c.set_rate(rate)
  c.play()
  c.step(1)
  assert not c.playing and c.frame == 21 and c.pos == pytest.approx(21 / FPS)
  c.step(-1)
  c.step(-1)
  assert c.frame == 19 and c.pos == pytest.approx(19 / FPS)


def test_stepping_clamps_at_both_ends():
  c = clock(n=10)
  c.step(-1)
  assert c.frame == 0 and c.pos == 0.0
  c.step(50)
  assert c.frame == 9
  c.step(1)
  assert c.frame == 9 and not c.ended


def test_a_tick_that_lands_on_a_frame_boundary_selects_that_frame():
  c = clock()
  c.seek(0.0)
  c.play()
  c.tick(0.05)
  assert c.frame == 1
  c.step(5)
  assert c.pos == pytest.approx(6 / FPS) and c.frame == 6


def test_playback_stops_on_the_last_frame_and_play_restarts_from_the_start():
  c = clock(n=20)
  c.play()
  for _ in range(100):
    c.tick(0.1)
  assert c.ended and not c.playing and c.frame == 19 and c.pos == pytest.approx(19 / FPS)
  c.play()
  assert c.playing and not c.ended and c.pos == 0.0


def test_pausing_and_toggling():
  c = clock()
  c.toggle()
  assert c.playing
  c.toggle()
  assert not c.playing
  c.tick(0.1)
  assert c.pos == 0.0   # paused clocks do not move


def test_long_frame_times_are_clamped_so_playback_never_leaps():
  c = clock()
  c.play()
  c.tick(30.0)
  assert c.pos == pytest.approx(MAX_TICK_DT)
  c.tick(-5.0)
  assert c.pos == pytest.approx(MAX_TICK_DT)


def test_seek_and_jump_clamp_to_the_clip():
  c = clock(n=100, pos=10.0)    # 100 frames at 20 fps is a 5 s clip, so 10.0 is clamped on creation
  assert c.pos == pytest.approx(99 / FPS)
  c.seek(-3)
  assert c.pos == 0.0
  c.jump(JUMP_S)
  assert c.pos == pytest.approx(99 / FPS)
  c.jump(-JUMP_S)
  assert c.pos == 0.0
  c.seek(2.0)
  c.jump(-JUMP_S / 5)
  assert c.pos == pytest.approx(1.0)
  c.seek_fraction(2.0)
  assert c.pos == pytest.approx(99 / FPS)
  c.seek_fraction(0.5)
  assert c.pos == pytest.approx(2.5)


def test_jump_while_playing_keeps_playing_and_leaves_the_ended_state():
  c = clock(n=400)
  c.play()
  c.jump(5.0)
  assert c.playing and c.pos == pytest.approx(5.0)
  c.seek(19.9)
  c.tick(0.25)
  assert c.ended
  c.jump(-5.0)
  assert not c.ended


def test_rate_snaps_to_an_allowed_value():
  c = clock()
  assert c.set_rate(0.3) == 0.25 and c.set_rate(7) == 2.0 and c.set_rate(0.01) == 0.1 and c.set_rate(1) == 1.0
  assert snap_rate(0.6) == 0.5


def test_playback_waits_for_a_frame_that_is_not_decoded_yet():
  c = clock()
  c.play()
  c.tick(0.05, available=lambda i: False)
  assert c.pos == 0.0 and c.stalled and c.playing
  c.tick(0.05, available=lambda i: i <= 1)
  assert c.frame == 1 and not c.stalled
  # still on the same frame: no need for a new one
  c.tick(0.01, available=lambda i: False)
  assert c.pos == pytest.approx(0.06) and not c.stalled


def test_stall_does_not_block_reaching_the_end():
  c = clock(n=10)
  c.seek(0.44)
  c.play()
  c.tick(0.25, available=lambda i: False)
  assert c.ended


def test_scrub_math_round_trips_and_clamps():
  assert scrub_fraction(150, 100, 200) == 0.25
  assert scrub_fraction(0, 100, 200) == 0.0 and scrub_fraction(900, 100, 200) == 1.0 and scrub_fraction(5, 100, 0) == 0.0
  assert scrub_x(30.0, 60.0, 100, 200) == 200
  assert scrub_x(-5, 60.0, 100, 200) == 100 and scrub_x(500, 60.0, 100, 200) == 300 and scrub_x(1, 0, 100, 200) == 100
  f = scrub_fraction(173, 100, 200)
  assert scrub_x(f * 60.0, 60.0, 100, 200) == pytest.approx(173)


def test_format_clock():
  assert format_clock(0) == '0:00.0' and format_clock(65.25) == '1:05.2' and format_clock(-3) == '0:00.0'


# ---- clip and event mapping --------------------------------------------------------------------------------------------------

def test_parts_are_laid_end_to_end():
  a, b = Part('s--0', fake_index(100)), Part('s--1', fake_index(60))
  clip = Clip([a, b])
  assert clip.count == 160 and clip.duration == pytest.approx(8.0)
  assert (a.start, b.start) == (0, 100) and b.t0 == pytest.approx(5.0)
  assert clip.times[100] == pytest.approx(5.0) and clip.times[-1] == pytest.approx(7.95)
  assert clip.locate(0) == (0, 0) and clip.locate(99) == (0, 99) and clip.locate(100) == (1, 0) and clip.locate(159) == (1, 59)
  assert clip.frame_at(5.0) == 100 and clip.frame_at(4.99) == 99 and clip.frame_at(-1) == 0 and clip.frame_at(99) == 159


def test_event_time_comes_from_the_encode_index_not_from_a_guess():
  part = Part('s--0', fake_index(1200), fake_qlog(1200, mono0=1000.0))
  clip = Clip([part], event_mono=1030.25)
  assert clip.event_time == pytest.approx(30.25)
  assert clip.start_position() == pytest.approx(25.25)
  assert clip.frame_at(clip.start_position()) == 505


def test_event_just_before_the_first_frame_extrapolates_and_clamps_to_zero():
  part = Part('s--0', fake_index(100), fake_qlog(100, mono0=1000.0))
  clip = Clip([part], event_mono=999.5)
  assert clip.event_time == 0.0 and clip.start_position() == 0.0


def test_event_time_in_the_second_part_includes_the_first_parts_length():
  prev, main = Part('s--3', fake_index(1200), fake_qlog(1200, 940.0)), Part('s--4', fake_index(1200), fake_qlog(1200, 1000.0))
  clip = Clip([prev, main], event_mono=1004.0, main=1)
  assert clip.event_time == pytest.approx(60.0 + 4.0)
  assert clip.start_position() == pytest.approx(59.0)


def test_unknown_event_time_opens_at_the_start():
  part = Part('s--0', fake_index(100), None)
  clip = Clip([part], event_mono=1000.0)
  assert clip.event_time is None and clip.start_position() == 0.0
  assert Clip([Part('s--0', fake_index(100), fake_qlog(100, 0.0))], event_mono=None).event_time is None


def test_a_qlog_without_frame_stamps_gives_no_mapping():
  q = QlogSummary(np.full(100, np.nan), np.empty(0), np.empty(0), np.empty(0, bool), np.empty(0, bool), np.empty(0, bool))
  assert Part('s--0', fake_index(100), q).local_time(5.0) is None


def test_neighbour_rules():
  assert needs_neighbours(3.0, 60.0) == (True, False)
  assert needs_neighbours(CONTEXT_S + 1, 60.0) == (False, False)
  assert needs_neighbours(57.0, 60.0) == (False, True)
  assert needs_neighbours(None, 60.0) == (False, False)
  assert needs_neighbours(0.0, 10.0) == (True, True)


def test_pre_roll_is_five_seconds():
  assert PRE_ROLL_S == 5.0


# ---- telemetry ---------------------------------------------------------------------------------------------------------------

def telemetry_clip():
  car = [(1000.0 + k * 0.1, 10.0 + k * 0.1, k in (20, 21), k == 5, False) for k in range(100)]
  return Clip([Part('s--0', fake_index(200), fake_qlog(200, 1000.0, car))], event_mono=1005.0)


def test_telemetry_lookup_returns_the_latest_sample_unless_stale():
  t = telemetry_clip().telemetry
  now = t.at(1.04)
  assert now['speed'] == pytest.approx(11.0, abs=0.01) and not now['brake'] and not now['left']
  assert t.at(2.05)['brake'] is True and t.at(0.55)['left'] is True
  assert t.at(-3.0) is None
  assert t.at(60.0) is None     # more than a second after the last sample


def test_telemetry_buckets_for_the_strip():
  t = telemetry_clip().telemetry
  speed, brake, blink = t.buckets(50, 10.0)
  assert speed.shape == (50,) and np.isfinite(speed).all()
  assert brake[10] and not brake[30] and blink[2] and not blink[40]
  s2, b2, k2 = t.buckets(50, 40.0)          # a longer clip leaves the tail without data
  assert np.isnan(s2[-1]) and not b2[-1]
  assert t.buckets(0, 10.0)[0].shape == (0,)


def test_no_telemetry_when_qlog_is_missing_or_empty():
  assert Clip([Part('s--0', fake_index(20), None)]).telemetry is None
  assert Clip([Part('s--0', fake_index(20), fake_qlog(20, 0.0))]).telemetry is None
