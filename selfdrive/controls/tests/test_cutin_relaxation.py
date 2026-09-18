"""Pure cut-in relaxation tests (v3). Runnable without the ACADOS solver: cutin_relaxation.py
has no casadi/acados import. See its module docstring for the overspeed invariant and the
v3 robustness rules (lead-loss hold, same-track cooldown, no-op guard) pinned here."""
import numpy as np
import pytest

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.cutin_relaxation import (
  CUTIN_COMFORT_BRAKE_MAX, CUTIN_STOP_DISTANCE_FLOOR, LEAD_LOSS_HOLD_S, MAX_RECOVERY_TIME_S,
  MAX_RELAXATION_TIME_S, MIN_RECOVERY_TIME_S, MIN_SPEED, NOOP_FRAC, SAME_TRACK_COOLDOWN_S,
  CutinRelaxationState, _gap_at_frac, _solve_frac, gap_targets, safe_obstacle_distance, update,
)

# main-james nominal values (long_mpc.py) -- standard personality
T_FOLLOW = 0.725
COMFORT_BRAKE = 6.0
STOP_DISTANCE = 4.5
V_EGO = 20.0  # m/s (~45 mph), above MIN_SPEED
BAKED_STOP_DISTANCE, BAKED_COMFORT_BRAKE, LEAD_DANGER_FACTOR = 6.0, 2.5, 0.75


def step(state, now, lead_status=True, confidence=0., track_id=-1, d_rel=1000., v_ego=V_EGO):
  return update(state, now, v_ego, lead_status=lead_status, cutin_confidence=confidence,
                lead_track_id=track_id, observed_d_rel=d_rel, nominal_t_follow=T_FOLLOW,
                nominal_comfort_brake=COMFORT_BRAKE, nominal_stop_distance=STOP_DISTANCE)


def cutin(state, now, track_id=7, d_rel=20.0, confidence=0.9):
  return step(state, now, confidence=confidence, track_id=track_id, d_rel=d_rel)


def nominal_gap(v=V_EGO):
  return safe_obstacle_distance(v, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)


NOMINAL = (STOP_DISTANCE, COMFORT_BRAKE)


class TestPassthrough:
  def test_no_confidence_is_nominal(self):
    state, sd, cb = step(CutinRelaxationState(), 0.)
    assert (sd, cb) == NOMINAL and state == CutinRelaxationState()

  def test_below_threshold_confidence_does_not_trigger(self):
    state, sd, cb = cutin(CutinRelaxationState(), 0., confidence=0.3)
    assert (sd, cb) == NOMINAL and state.active_track_id == -1

  def test_speed_gate_is_immediate(self):
    state, _, _ = cutin(CutinRelaxationState(), 0.)
    state, sd, cb = step(state, 0.05, confidence=0., track_id=7, d_rel=20., v_ego=MIN_SPEED - 0.1)
    assert (sd, cb) == NOMINAL and state == CutinRelaxationState()


class TestSolve:
  def test_frac_endpoints(self):
    _, sd, cb = _gap_at_frac(0., V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)
    assert (sd, cb) == NOMINAL
    _, sd, cb = _gap_at_frac(1., V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)
    assert (sd, cb) == pytest.approx((CUTIN_STOP_DISTANCE_FLOOR, CUTIN_COMFORT_BRAKE_MAX))

  def test_gap_monotone_and_bisection_converges(self):
    gaps = [_gap_at_frac(f / 20, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0] for f in range(21)]
    assert all(a >= b for a, b in zip(gaps[:-1], gaps[1:], strict=True))
    for target in (gaps[0], (gaps[0] + gaps[-1]) / 2, gaps[-1]):
      frac = _solve_frac(target, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)
      assert _gap_at_frac(frac, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0] == pytest.approx(target, abs=0.05)

  def test_floor_is_about_55_percent_of_nominal_at_70mph(self):
    v = 70 / 2.237
    floor_gap = _gap_at_frac(1., v, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0]
    assert floor_gap / nominal_gap(v) == pytest.approx(0.55, abs=0.01)


class TestOverspeedGuard:
  """v2 root cause: cruise obstacle placed with the nominal pair while the cost target used
  the relaxed pair. gap_targets() must derive both from the same pair."""

  def test_cruise_gap_equals_comfort_target_gap_while_relaxed(self):
    state, sd, cb = cutin(CutinRelaxationState(), 0.)
    assert (sd, cb) != NOMINAL
    v_cruise = np.full(13, V_EGO)
    t = gap_targets(V_EGO, v_cruise, T_FOLLOW, sd, cb, BAKED_STOP_DISTANCE, BAKED_COMFORT_BRAKE, LEAD_DANGER_FACTOR)
    assert (t.stop_distance, t.comfort_brake) == (sd, cb)
    # at v_cruise == v_ego the cruise obstacle gap IS the comfort target gap
    assert np.allclose(t.cruise_gap, t.comfort_now)
    assert t.comfort_now == pytest.approx(safe_obstacle_distance(V_EGO, T_FOLLOW, sd, cb))

  def test_danger_factor_tracks_relaxed_target(self):
    state, sd, cb = cutin(CutinRelaxationState(), 0.)
    t = gap_targets(V_EGO, np.full(13, V_EGO), T_FOLLOW, sd, cb, BAKED_STOP_DISTANCE, BAKED_COMFORT_BRAKE, LEAD_DANGER_FACTOR)
    baked = safe_obstacle_distance(V_EGO, T_FOLLOW, BAKED_STOP_DISTANCE, BAKED_COMFORT_BRAKE)
    assert t.danger_factor == pytest.approx(LEAD_DANGER_FACTOR * t.comfort_now / baked)
    assert t.danger_factor < LEAD_DANGER_FACTOR


class TestRecovery:
  def test_relaxes_to_observed_gap_then_recovers(self):
    state, sd, cb = cutin(CutinRelaxationState(), 0., d_rel=40.)  # nominal ~52 m, floor ~32 m
    assert safe_obstacle_distance(V_EGO, T_FOLLOW, sd, cb) == pytest.approx(40., abs=0.1)
    assert state.recovery_time < MAX_RECOVERY_TIME_S
    _, sd_mid, cb_mid = step(state, state.recovery_time / 2, track_id=7, d_rel=40.)
    assert STOP_DISTANCE >= sd_mid >= sd and COMFORT_BRAKE <= cb_mid <= cb
    state, sd, cb = step(state, state.recovery_time + 0.01, track_id=7, d_rel=40.)
    assert (sd, cb) == NOMINAL and state.active_track_id == -1

  def test_severity_sets_recovery_time(self):
    floor_gap = _gap_at_frac(1., V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0]
    severe, _, _ = cutin(CutinRelaxationState(), 0., d_rel=floor_gap - 5.)
    mild, _, _ = cutin(CutinRelaxationState(), 0., d_rel=nominal_gap() - 3.)
    assert severe.recovery_time == pytest.approx(MIN_RECOVERY_TIME_S)
    assert mild.recovery_time > 0.8 * MAX_RECOVERY_TIME_S

  def test_repeat_cutin_keeps_progress_and_streak_cap(self):
    state, _, _ = cutin(CutinRelaxationState(), 0., track_id=1, d_rel=40.)
    now = state.recovery_time / 2
    current_frac = state.start_frac * 0.5
    current_gap = _gap_at_frac(current_frac, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0]
    # a new cut-in observed slightly wider than the current target must not loosen it back up
    state2, sd, cb = cutin(state, now, track_id=2, d_rel=current_gap + 0.5)
    assert state2.active_track_id == 1 and state2.recovery_started_mono == 0.
    # a tighter one takes the tighter gap and keeps the streak clock
    state2, _, _ = cutin(state, now, track_id=2, d_rel=5.)
    assert state2.active_track_id == 2 and state2.start_frac >= current_frac
    assert state2.streak_started_mono == 0.
    # keep chaining past the streak cap: further cut-ins are ignored
    s, t = state2, now
    for tid in range(3, 40):
      t += s.recovery_time * 0.9
      s, _, _ = cutin(s, t, track_id=tid, d_rel=5.)
      if t - s.streak_started_mono >= MAX_RELAXATION_TIME_S:
        break
    ignored, _, _ = cutin(s, t + 0.1, track_id=99, d_rel=5.)
    assert ignored.active_track_id == s.active_track_id


class TestV3Robustness:
  def test_lead_flicker_does_not_end_event(self):
    state, sd, cb = cutin(CutinRelaxationState(), 0.)
    held, sd2, cb2 = step(state, 0.05, lead_status=False)
    assert held.active_track_id == 7 and held.lead_lost_since == pytest.approx(0.05)
    assert (sd2, cb2) != NOMINAL
    back, _, _ = step(held, 0.10, track_id=7, d_rel=20.)
    assert back.active_track_id == 7 and back.lead_lost_since == -1.
    assert back.recovery_started_mono == 0.  # same event, not restarted

  def test_lead_lost_for_hold_time_ends_event(self):
    state, _, _ = cutin(CutinRelaxationState(), 0.)
    state, _, _ = step(state, 1.0, lead_status=False)
    state, sd, cb = step(state, 1.0 + LEAD_LOSS_HOLD_S, lead_status=False)
    assert state.active_track_id == -1 and (sd, cb) == NOMINAL
    assert state.cooldown_track_id == 7

  def test_same_track_cooldown_blocks_retrigger(self):
    state, _, _ = cutin(CutinRelaxationState(), 0.)
    end = state.recovery_time + 0.01
    state, _, _ = step(state, end, track_id=7, d_rel=20.)
    assert state.active_track_id == -1
    again, sd, cb = cutin(state, end + 0.1, track_id=7)
    assert again.active_track_id == -1 and (sd, cb) == NOMINAL
    other, _, _ = cutin(state, end + 0.1, track_id=8)
    assert other.active_track_id == 8
    later, _, _ = cutin(state, end + SAME_TRACK_COOLDOWN_S + 0.1, track_id=7)
    assert later.active_track_id == 7

  def test_flicker_retrigger_after_hold_is_blocked_by_cooldown(self):
    state, _, _ = cutin(CutinRelaxationState(), 0.)
    state, _, _ = step(state, 2.0, lead_status=False)
    state, _, _ = step(state, 2.0 + LEAD_LOSS_HOLD_S, lead_status=False)
    again, _, _ = cutin(state, 2.6, track_id=7)  # radard latch still says 0.9 for the same track
    assert again.active_track_id == -1

  def test_noop_when_observed_gap_already_at_target(self):
    far = nominal_gap() + 50.
    state, sd, cb = cutin(CutinRelaxationState(), 0., d_rel=far)
    assert state.active_track_id == -1 and (sd, cb) == NOMINAL

  def test_noop_threshold_boundary(self):
    just_under = _gap_at_frac(NOOP_FRAC / 2, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0]
    state, _, _ = cutin(CutinRelaxationState(), 0., d_rel=just_under)
    assert state.active_track_id == -1
    clearly = _gap_at_frac(NOOP_FRAC * 3, V_EGO, T_FOLLOW, STOP_DISTANCE, COMFORT_BRAKE)[0]
    state, _, _ = cutin(CutinRelaxationState(), 0., d_rel=clearly)
    assert state.active_track_id == 7

  def test_noop_during_recovery_keeps_existing_event(self):
    state, _, _ = cutin(CutinRelaxationState(), 0., track_id=1, d_rel=20.)
    same, _, _ = cutin(state, 1.0, track_id=2, d_rel=nominal_gap() + 50.)
    assert same.active_track_id == 1 and same.recovery_started_mono == 0.
