"""Pure cut-in relaxation tests — importable and runnable without the ACADOS solver
(cutin_relaxation.py has no casadi/acados import). See gap_params.py's own test file for
the same pattern, and cutin_relaxation.py's docstring for why only stop_distance is
touched (never t_follow/comfort_brake)."""
import pytest

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.cutin_relaxation import (
  CutinRelaxationState, MAX_RELAXATION_TIME_S, MIN_SPEED, RECOVERY_TIME_S, update,
)
from opendbc.car.gm.volt_following import STOP_DISTANCE_BOUNDS

T_FOLLOW = 1.45
COMFORT_BRAKE = 2.5
NOMINAL_STOP_DISTANCE = 6.0
V_EGO = 20.0  # m/s, above MIN_SPEED


def no_cutin(state, now):
  return update(state, now, V_EGO, lead_status=True, cutin_confidence=0., lead_track_id=-1,
                observed_d_rel=100., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                nominal_stop_distance=NOMINAL_STOP_DISTANCE)


def cutin(state, now, track_id=7, observed_d_rel=8.0, confidence=0.9):
  return update(state, now, V_EGO, lead_status=True, cutin_confidence=confidence, lead_track_id=track_id,
                observed_d_rel=observed_d_rel, nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                nominal_stop_distance=NOMINAL_STOP_DISTANCE)


class TestNoCutinPassthrough:
  def test_no_lead_returns_nominal_and_resets_state(self):
    state, stop_distance = update(CutinRelaxationState(active_track_id=7, streak_started_mono=0.,
                                                         recovery_started_mono=0., start_stop_distance=5.5),
                                   now=1., v_ego=V_EGO, lead_status=False, cutin_confidence=0.9, lead_track_id=7,
                                   observed_d_rel=8., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                                   nominal_stop_distance=NOMINAL_STOP_DISTANCE)
    assert stop_distance == NOMINAL_STOP_DISTANCE
    assert state == CutinRelaxationState()

  def test_below_min_speed_returns_nominal_and_resets_state(self):
    state, stop_distance = update(CutinRelaxationState(active_track_id=7, streak_started_mono=0.,
                                                         recovery_started_mono=0., start_stop_distance=5.5),
                                   now=1., v_ego=MIN_SPEED - 1., lead_status=True, cutin_confidence=0.9, lead_track_id=7,
                                   observed_d_rel=8., nominal_t_follow=T_FOLLOW, nominal_comfort_brake=COMFORT_BRAKE,
                                   nominal_stop_distance=NOMINAL_STOP_DISTANCE)
    assert stop_distance == NOMINAL_STOP_DISTANCE
    assert state == CutinRelaxationState()

  def test_no_confidence_passes_nominal_through_unchanged(self):
    state, stop_distance = no_cutin(CutinRelaxationState(), now=0.)
    assert stop_distance == NOMINAL_STOP_DISTANCE
    assert state == CutinRelaxationState()

  def test_low_confidence_below_threshold_does_not_trigger(self):
    state, stop_distance = cutin(CutinRelaxationState(), now=0., confidence=0.1)
    assert stop_distance == NOMINAL_STOP_DISTANCE
    assert state.active_track_id == -1


class TestRelaxationRamp:
  def test_cutin_immediately_relaxes_below_nominal(self):
    state, stop_distance = cutin(CutinRelaxationState(), now=0.)
    assert stop_distance < NOMINAL_STOP_DISTANCE
    assert stop_distance >= STOP_DISTANCE_BOUNDS[0]
    assert state.active_track_id == 7

  def test_never_floors_below_shared_bounds_even_for_a_very_tight_merge(self):
    state, stop_distance = cutin(CutinRelaxationState(), now=0., observed_d_rel=0.5)
    assert stop_distance == pytest.approx(STOP_DISTANCE_BOUNDS[0])

  def test_never_requests_more_than_nominal_gap(self):
    # a "cut-in" that leaves MORE than the nominal gap shouldn't widen the target
    state, stop_distance = cutin(CutinRelaxationState(), now=0., observed_d_rel=1000.)
    assert stop_distance == pytest.approx(NOMINAL_STOP_DISTANCE)

  def test_recovers_linearly_back_to_nominal_over_recovery_time(self):
    state, start_stop_distance = cutin(CutinRelaxationState(), now=0.)
    _, mid_stop_distance = no_cutin(state, now=RECOVERY_TIME_S / 2)
    _, end_stop_distance = no_cutin(state, now=RECOVERY_TIME_S)
    assert start_stop_distance < mid_stop_distance < end_stop_distance
    assert mid_stop_distance == pytest.approx((start_stop_distance + NOMINAL_STOP_DISTANCE) / 2, abs=1e-6)
    assert end_stop_distance == pytest.approx(NOMINAL_STOP_DISTANCE)

  def test_fully_recovered_state_resets_and_allows_a_fresh_cutin_later(self):
    state, _ = cutin(CutinRelaxationState(), now=0.)
    state, stop_distance = no_cutin(state, now=RECOVERY_TIME_S + 1.)
    assert stop_distance == NOMINAL_STOP_DISTANCE
    assert state == CutinRelaxationState()
    # a brand new cut-in well after full recovery starts its own fresh streak
    state, stop_distance = cutin(state, now=RECOVERY_TIME_S + 5., track_id=9)
    assert state.active_track_id == 9
    assert state.streak_started_mono == RECOVERY_TIME_S + 5.


class TestExistingLeadDoesNotRetrigger:
  def test_same_track_id_persisting_does_not_reset_the_ramp(self):
    state, first = cutin(CutinRelaxationState(), now=0.)
    # same track, still "confident" every cycle (as it would be while it remains selected) --
    # must not be treated as a new cut-in each time, or recovery would never progress
    state, second = cutin(state, now=1., confidence=0.9)
    assert second > first  # still recovering, not reset to the same relaxed start
    assert state.streak_started_mono == 0.


class TestRepeatedCutins:
  def test_repeated_cutin_mid_recovery_keeps_prior_progress_as_new_start(self):
    state, start = cutin(CutinRelaxationState(), now=0., observed_d_rel=8.0)
    state, partial = no_cutin(state, now=RECOVERY_TIME_S / 2)
    # a second, different vehicle cuts in before full recovery
    state2, restarted = cutin(state, now=RECOVERY_TIME_S / 2, track_id=11, observed_d_rel=8.0)
    # the new relaxed start should be based on the already-recovered `partial` gap, not `start`
    assert restarted == pytest.approx(min(max(8.0 - T_FOLLOW * V_EGO - V_EGO**2/(2*COMFORT_BRAKE),
                                               STOP_DISTANCE_BOUNDS[0]), partial))
    assert state2.streak_started_mono == 0.  # streak clock keeps running from the first cut-in

  def test_streak_cap_blocks_further_cutins_once_exceeded(self):
    # gaps of RECOVERY_TIME_S/4 keep `recovering` true continuously (each gap < RECOVERY_TIME_S),
    # so the streak clock accumulates from the very first cut-in without ever resetting
    state = CutinRelaxationState()
    now = 0.
    for i in range(4):
      state, _ = cutin(state, now=now, track_id=100 + i, observed_d_rel=8.0)
      now += RECOVERY_TIME_S * 3 / 4  # 15s steps, under the 20s RECOVERY_TIME_S
    assert now - state.streak_started_mono == pytest.approx(MAX_RELAXATION_TIME_S)
    last_accepted_track = state.active_track_id

    # right at the cap boundary, a further "new" cut-in must be rejected -- state doesn't adopt it
    state, _ = cutin(state, now=now, track_id=999, observed_d_rel=8.0)
    assert state.active_track_id == last_accepted_track
