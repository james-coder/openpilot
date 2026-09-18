"""Pure cut-in relaxation tests — importable and runnable without the ACADOS solver
(cutin_relaxation.py has no casadi/acados import). See gap_params.py's own test file for
the same pattern, and cutin_relaxation.py's docstring for why stop_distance/comfort_brake
(never t_follow) are touched, and why their bounds here are wider than the personality-
validation bounds in opendbc.car.gm.volt_following."""
import pytest

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.cutin_relaxation import (
  CUTIN_COMFORT_BRAKE_MAX, CUTIN_STOP_DISTANCE_FLOOR, MAX_RECOVERY_TIME_S, MAX_RELAXATION_TIME_S,
  MIN_RECOVERY_TIME_S, MIN_SPEED, CutinRelaxationState, _gap_at_frac, _solve_frac, update,
)
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.gap_params import get_safe_obstacle_distance
from opendbc.car.gm.volt_following import MANUAL_INTERIM_PROFILE

T_FOLLOW = 1.45
COMFORT_BRAKE = 2.5
NOMINAL_STOP_DISTANCE = 6.0
V_EGO = 20.0  # m/s, above MIN_SPEED


def no_cutin(state, now):
  return update(state, now, V_EGO, lead_status=True, cutin_confidence=0., lead_track_id=-1,
                observed_d_rel=1000., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                nominal_stop_distance=NOMINAL_STOP_DISTANCE)


def cutin(state, now, track_id=7, observed_d_rel=8.0, confidence=0.9):
  return update(state, now, V_EGO, lead_status=True, cutin_confidence=confidence, lead_track_id=track_id,
                observed_d_rel=observed_d_rel, nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                nominal_stop_distance=NOMINAL_STOP_DISTANCE)


def nominal_gap():
  return get_safe_obstacle_distance(V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)


class TestNoCutinPassthrough:
  def test_no_lead_returns_nominal_and_resets_state(self):
    active = CutinRelaxationState(active_track_id=7, streak_started_mono=0., recovery_started_mono=0.,
                                   recovery_time=MIN_RECOVERY_TIME_S, start_frac=1.)
    state, stop_distance, comfort_brake = update(
      active, now=1., v_ego=V_EGO, lead_status=False, cutin_confidence=0.9, lead_track_id=7,
      observed_d_rel=8., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
      nominal_stop_distance=NOMINAL_STOP_DISTANCE)
    assert (stop_distance, comfort_brake) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert state == CutinRelaxationState()

  def test_below_min_speed_returns_nominal_and_resets_state(self):
    active = CutinRelaxationState(active_track_id=7, streak_started_mono=0., recovery_started_mono=0.,
                                   recovery_time=MIN_RECOVERY_TIME_S, start_frac=1.)
    state, stop_distance, comfort_brake = update(
      active, now=1., v_ego=MIN_SPEED - 1., lead_status=True, cutin_confidence=0.9, lead_track_id=7,
      observed_d_rel=8., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
      nominal_stop_distance=NOMINAL_STOP_DISTANCE)
    assert (stop_distance, comfort_brake) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert state == CutinRelaxationState()

  def test_no_confidence_passes_nominal_through_unchanged(self):
    state, stop_distance, comfort_brake = no_cutin(CutinRelaxationState(), now=0.)
    assert (stop_distance, comfort_brake) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert state == CutinRelaxationState()

  def test_low_confidence_below_threshold_does_not_trigger(self):
    state, stop_distance, comfort_brake = cutin(CutinRelaxationState(), now=0., confidence=0.1)
    assert (stop_distance, comfort_brake) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert state.active_track_id == -1


class TestGapAtFracAndSolveFrac:
  def test_frac_zero_reproduces_nominal_exactly(self):
    gap, sd, cb = _gap_at_frac(0., V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert (sd, cb) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert gap == pytest.approx(nominal_gap())

  def test_frac_one_reproduces_the_floor_exactly(self):
    gap, sd, cb = _gap_at_frac(1., V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert (sd, cb) == pytest.approx((CUTIN_STOP_DISTANCE_FLOOR, CUTIN_COMFORT_BRAKE_MAX))

  def test_gap_at_frac_is_monotonically_non_increasing(self):
    gaps = [_gap_at_frac(f / 10, V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)[0] for f in range(11)]
    assert all(a >= b for a, b in zip(gaps, gaps[1:], strict=False))

  def test_solve_frac_converges_to_the_requested_gap(self):
    target = nominal_gap() * 0.7
    frac = _solve_frac(target, V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    gap, _, _ = _gap_at_frac(frac, V_EGO, T_FOLLOW, NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert gap == pytest.approx(target, abs=1e-3)


class TestRelaxationRamp:
  def test_cutin_relaxes_both_stop_distance_and_comfort_brake(self):
    state, stop_distance, comfort_brake = cutin(CutinRelaxationState(), now=0.)
    assert stop_distance < NOMINAL_STOP_DISTANCE
    assert comfort_brake > COMFORT_BRAKE
    assert stop_distance >= CUTIN_STOP_DISTANCE_FLOOR
    assert comfort_brake <= CUTIN_COMFORT_BRAKE_MAX
    assert state.active_track_id == 7

  def test_never_floors_below_the_wider_cutin_bounds_even_for_a_very_tight_merge(self):
    state, stop_distance, comfort_brake = cutin(CutinRelaxationState(), now=0., observed_d_rel=0.5)
    assert (stop_distance, comfort_brake) == pytest.approx((CUTIN_STOP_DISTANCE_FLOOR, CUTIN_COMFORT_BRAKE_MAX))

  def test_never_requests_more_than_nominal_gap(self):
    # a "cut-in" that leaves MORE than the nominal gap shouldn't widen the target
    state, stop_distance, comfort_brake = cutin(CutinRelaxationState(), now=0., observed_d_rel=100000.)
    assert stop_distance == pytest.approx(NOMINAL_STOP_DISTANCE, abs=1e-3)
    assert comfort_brake == pytest.approx(COMFORT_BRAKE, abs=1e-3)

  def test_recovers_monotonically_back_to_nominal_over_its_own_recovery_time(self):
    state, start_sd, start_cb = cutin(CutinRelaxationState(), now=0.)
    recovery_time = state.recovery_time
    _, mid_sd, mid_cb = no_cutin(state, now=recovery_time / 2)
    _, end_sd, end_cb = no_cutin(state, now=recovery_time)
    assert start_sd < mid_sd < end_sd
    assert start_cb > mid_cb > end_cb
    assert (end_sd, end_cb) == pytest.approx((NOMINAL_STOP_DISTANCE, COMFORT_BRAKE))

  def test_fully_recovered_state_resets_and_allows_a_fresh_cutin_later(self):
    state, _, _ = cutin(CutinRelaxationState(), now=0.)
    recovery_time = state.recovery_time
    state, stop_distance, comfort_brake = no_cutin(state, now=recovery_time + 1.)
    assert (stop_distance, comfort_brake) == (NOMINAL_STOP_DISTANCE, COMFORT_BRAKE)
    assert state == CutinRelaxationState()
    # a brand new cut-in well after full recovery starts its own fresh streak
    state, _, _ = cutin(state, now=recovery_time + 5., track_id=9)
    assert state.active_track_id == 9
    assert state.streak_started_mono == recovery_time + 5.


class TestAdaptiveRecoveryTime:
  def test_severe_near_floor_cutin_recovers_close_to_the_minimum_time(self):
    state, _, _ = cutin(CutinRelaxationState(), now=0., observed_d_rel=0.1)  # pinned at the floor
    assert state.start_frac == pytest.approx(1., abs=1e-3)
    assert state.recovery_time == pytest.approx(MIN_RECOVERY_TIME_S, abs=0.1)

  def test_mild_barely_relaxed_cutin_recovers_close_to_the_maximum_time(self):
    # a gap only trivially below nominal
    state, _, _ = cutin(CutinRelaxationState(), now=0., observed_d_rel=nominal_gap() - 0.01)
    assert state.start_frac == pytest.approx(0., abs=1e-2)
    assert state.recovery_time == pytest.approx(MAX_RECOVERY_TIME_S, abs=0.5)

  def test_moderate_cutin_recovers_between_the_two_bounds(self):
    state, _, _ = cutin(CutinRelaxationState(), now=0., observed_d_rel=nominal_gap() * 0.6)
    assert MIN_RECOVERY_TIME_S < state.recovery_time < MAX_RECOVERY_TIME_S


class TestExistingLeadDoesNotRetrigger:
  def test_same_track_id_persisting_does_not_reset_the_ramp(self):
    state, first_sd, _ = cutin(CutinRelaxationState(), now=0.)
    # same track, still "confident" every cycle (as it would be while it remains selected) --
    # must not be treated as a new cut-in each time, or recovery would never progress
    state, second_sd, _ = cutin(state, now=1., confidence=0.9)
    assert second_sd > first_sd  # still recovering, not reset to the same relaxed start
    assert state.streak_started_mono == 0.


class TestRepeatedCutins:
  def test_repeated_cutin_mid_recovery_keeps_prior_progress_as_new_start(self):
    state, _, _ = cutin(CutinRelaxationState(), now=0., observed_d_rel=8.0)
    recovery_time = state.recovery_time
    state, partial_sd, partial_cb = no_cutin(state, now=recovery_time / 2)
    partial_gap = get_safe_obstacle_distance(V_EGO, T_FOLLOW, partial_sd, partial_cb)
    # a second, different vehicle cuts in before full recovery -- the new relaxed start must
    # not loosen back past whatever gap had already been recovered to
    state2, restarted_sd, restarted_cb = cutin(state, now=recovery_time / 2, track_id=11, observed_d_rel=8.0)
    restarted_gap = get_safe_obstacle_distance(V_EGO, T_FOLLOW, restarted_sd, restarted_cb)
    assert restarted_gap <= partial_gap + 1e-6
    assert state2.streak_started_mono == 0.  # streak clock keeps running from the first cut-in

  def test_streak_cap_blocks_further_cutins_once_exceeded(self):
    # severe (near-floor) cut-ins get a deterministic MIN_RECOVERY_TIME_S recovery; step under
    # that keeps `recovering` continuously true, so the streak clock accumulates exactly
    state = CutinRelaxationState()
    now = 0.
    step = MIN_RECOVERY_TIME_S * 0.75  # 6s, under the 8s MIN_RECOVERY_TIME_S
    n = int(MAX_RELAXATION_TIME_S / step)  # exact number of steps to land on the cap boundary
    for i in range(n):
      state, _, _ = cutin(state, now=now, track_id=100 + i, observed_d_rel=0.1)
      now += step
    assert now - state.streak_started_mono == pytest.approx(MAX_RELAXATION_TIME_S, abs=1e-6)
    last_accepted_track = state.active_track_id

    # right at the cap boundary, a further "new" cut-in must be rejected -- state doesn't adopt it
    state, _, _ = cutin(state, now=now, track_id=999, observed_d_rel=0.1)
    assert state.active_track_id == last_accepted_track


class TestRealisticHighwayCutinsHaveMeaningfulRange:
  """Regression coverage for the bug this round's review caught: V1's relaxation reused the
  personality-validation bounds as its floor, leaving ~11% of the nominal gap relaxable for
  the aggressive tier at highway speed -- nowhere near enough to absorb a real cut-in. These
  assert the fix actually gives real headroom for all three tiers at realistic speeds."""

  @pytest.mark.parametrize('mph', [30, 50, 70])
  @pytest.mark.parametrize('tier_name', ['aggressive', 'standard', 'relaxed'])
  def test_relaxation_range_is_a_substantial_fraction_of_nominal(self, tier_name, mph):
    tier = getattr(MANUAL_INTERIM_PROFILE, tier_name)
    v = mph * 0.44704
    nominal = get_safe_obstacle_distance(v, tier.t_follow, tier.stop_distance, tier.comfort_brake)
    floor, _, _ = _gap_at_frac(1., v, tier.t_follow, tier.stop_distance, tier.comfort_brake)
    # at least 30% of the nominal gap must be relaxable at every tier/speed combination
    assert (nominal - floor) / nominal > 0.3, f'{tier_name} at {mph}mph: only {nominal - floor:.1f}m of {nominal:.1f}m relaxable'

  def test_a_generous_post_merge_gap_is_honored_close_to_exactly_not_clamped(self):
    # the concrete example used to explain this fix: aggressive, 70mph, a 40m gap left behind
    # (generous, not scary) should be accepted close to as-is, not clamped up toward nominal
    tier = MANUAL_INTERIM_PROFILE.aggressive
    v = 70 * 0.44704
    state, _, _ = update(CutinRelaxationState(), now=0., v_ego=v, lead_status=True, cutin_confidence=0.9,
                          lead_track_id=7, observed_d_rel=40., nominal_t_follow=tier.t_follow,
                          nominal_comfort_brake=tier.comfort_brake, nominal_stop_distance=tier.stop_distance)
    _, sd, cb = _gap_at_frac(state.start_frac, v, tier.t_follow, tier.stop_distance, tier.comfort_brake)
    achieved_gap = get_safe_obstacle_distance(v, tier.t_follow, sd, cb)
    assert achieved_gap == pytest.approx(40., abs=1.0)
