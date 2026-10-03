import numpy as np
import pytest

from openpilot.system.review.detector import EventDetector, G

RNG = np.random.default_rng(7)
UP = np.array([0.0, 0.6, 0.8])        # a tilted windshield mount: gravity is not along any device axis
FWD = np.array([1.0, 0.0, 0.0])
LEFT = np.array([0.0, 0.8, -0.6])


def drive(det, speed, long_g=None, lat_g=None, duration=30.0, t0=0.0, brake=None, noise=0.04, jolts=(), wheel=None):
  """Feed 100 Hz carState and 104 Hz accelerometer. speed(t) -> m/s (wheel speed), long_g(t) -> IMU g (negative = braking),
  brake(t) -> bool, jolts: [(t, g)] single-sample vertical spikes. Returns the events produced."""
  out = []
  long_g = long_g or (lambda t: 0.0)
  lat_g = lat_g or (lambda t: 0.0)
  brake = brake or (lambda t: False)
  wheel = wheel or speed
  n_cs, n_acc = int(duration * 100), int(duration * 104)
  i = j = 0
  while i < n_cs or j < n_acc:
    tc = t0 + i / 100 if i < n_cs else 1e18
    ta = t0 + j / 104 if j < n_acc else 1e18
    if tc <= ta:
      out += det.car_state(tc, wheel(tc - t0), brake(tc - t0))
      i += 1
    else:
      t = ta - t0
      a = UP * G + FWD * long_g(t) * G + LEFT * lat_g(t) * G + RNG.normal(0, noise, 3)
      for jt, jg in jolts:
        if abs(t - jt) < 0.5 / 104:
          a = a + UP * jg * G
      out += det.accel(ta, *a)
      j += 1
  return out


def ends(events):
  return [e for e in events if e['type'] == 'end']


def stop_profile(v0, decel_g, t_start):
  def v(t):
    if t < t_start:
      return v0
    return max(v0 - decel_g * G * (t - t_start), 0.0)
  return v


def test_steady_driving_and_bumps_make_no_events():
  det = EventDetector()
  ev = drive(det, lambda t: 15.0, duration=60, jolts=[(10, 0.5), (25, 0.6), (40, 0.55)])
  ev += det.tick(1e6)
  assert ev == []


@pytest.mark.parametrize('decel_g', [0.15, 0.25, 0.35])
def test_ordinary_and_firm_stops_make_no_events(decel_g):
  det = EventDetector()
  v = stop_profile(15.0, decel_g, 5.0)
  ev = drive(det, v, long_g=lambda t: -decel_g if 5 <= t < 5 + 15 / (decel_g * G) else 0.0, brake=lambda t: t >= 5, duration=30)
  ev += det.tick(1e6)
  assert ev == []


def test_hard_stop_is_one_episode_with_peaks_and_an_end_record():
  det = EventDetector()
  v = stop_profile(15.0, 0.6, 5.0)
  ev = drive(det, v, long_g=lambda t: -0.6 if 5 <= t < 7.55 else 0.0, brake=lambda t: t >= 5, duration=20)
  ev += det.tick(1e6)
  starts = [e for e in ev if e['type'] == 'start']
  assert len(starts) == 1 and 'hard_brake' in (starts[0]['kinds'] + [k for e in ev for k in e['kinds']])
  assert 5.3 <= starts[0]['t'] <= 6.0, starts[0]['t']  # fires after the 0.3 s sustain, not at once
  end = ends(ev)
  assert len(end) == 1 and end[0]['peaks']['decel_g'] >= 0.55


def test_wheel_speed_spike_without_brake_pedal_is_not_an_event():
  det = EventDetector()
  v = stop_profile(12.0, 1.5, 5.0)  # wheels drop fast (a wheel-speed glitch / skid on throttle) but no pedal, IMU calm
  ev = drive(det, v, brake=lambda t: False, duration=15)
  ev += det.tick(1e6)
  assert ev == []


def test_abs_like_wheel_lock_is_detected_when_wheels_decelerate_far_more_than_the_car():
  det = EventDetector()
  # tonight's signature: pedal hard, wheels pulse down at ~1.5 g while the IMU sees about 0.7 g
  v = stop_profile(5.8, 1.5, 5.0)
  ev = drive(det, v, long_g=lambda t: -0.7 if 5 <= t < 6 else 0.0, brake=lambda t: t >= 5.0, duration=12)
  ev += det.tick(1e6)
  kinds = {k for e in ev for k in e['kinds']}
  assert 'abs_stop' in kinds
  assert len([e for e in ev if e['type'] == 'start']) == 1


def test_extreme_horizontal_g_is_detected_with_a_tilted_mount():
  det = EventDetector()
  ev = drive(det, lambda t: 10.0, long_g=lambda t: -1.2 if 8 <= t < 8.3 else 0.0, duration=20)
  ev += det.tick(1e6)
  assert any('extreme_g' in e['kinds'] for e in ev)


def test_vertical_jolts_are_never_events_however_large_potholes_are():
  det = EventDetector()
  ev = drive(det, lambda t: 10.0, duration=20, jolts=[(8, 0.6), (12, 1.6), (15, 2.5)])  # vertical spikes
  ev += det.tick(1e6)
  assert ev == []
  assert det.stats['jolt_g'] > 1.5 and det.stats['jolt_h_g'] < 0.3  # measured, just not acted on


def test_sharp_horizontal_jolt_is_an_impact_once_enabled():
  from openpilot.system.review.detector import Thresholds
  det = EventDetector(Thresholds(impact_jolt_g=0.6))
  ev = drive(det, lambda t: 10.0, long_g=lambda t: -0.8 if 12.0 <= t < 12.012 else 0.0, duration=20)  # a ~12 ms horizontal knock
  ev += det.tick(1e6)
  assert any('impact' in e['kinds'] for e in ev)
  quiet = EventDetector()  # default thresholds: disabled
  assert drive(quiet, lambda t: 10.0, long_g=lambda t: -0.8 if 12.0 <= t < 12.012 else 0.0, duration=20) == []


def test_events_close_together_merge_and_a_later_one_starts_a_new_episode():
  det = EventDetector()
  def big(t):
    return -1.2 if (8 <= t < 8.3 or 10 <= t < 10.3 or 30 <= t < 30.3) else 0.0
  ev = drive(det, lambda t: 10.0, long_g=big, duration=40)
  ev += det.tick(1e6)
  assert len([e for e in ev if e['type'] == 'start']) == 2
  assert len(ends(ev)) == 2


def test_nothing_fires_during_warmup_and_gaps_reset_the_windows():
  det = EventDetector()
  ev = drive(det, lambda t: 10.0, long_g=lambda t: -1.5 if 1.0 <= t < 1.3 else 0.0, duration=2.5)  # inside the 3 s warm-up
  assert ev == []
  # a long gap in carState, then a speed that looks like a crash-stop across the gap: must not be treated as one stop
  det = EventDetector()
  ev = det.car_state(0.0, 20.0, True) + det.car_state(0.01, 20.0, True) + det.car_state(2.0, 0.0, True) + det.car_state(2.01, 0.0, True)
  assert ev == []


def test_garbage_gravity_reading_never_raises_or_fires():
  det = EventDetector()
  out = []
  for k in range(2000):
    out += det.accel(k / 104, 0.0, 0.0, 0.0)  # sensor returning zeros
  out += det.car_state(5.0, 10.0, False)
  assert out == []
