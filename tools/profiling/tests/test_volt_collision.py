from dataclasses import replace
import math
import numpy as np
import pytest

from openpilot.tools.profiling.volt_collision import Envelope, assess, experiments, maximum_closure, motion


BOUNDS = Envelope(.5, 4., 8., 1., .5, .15, 2.5)


def check(gap=24., ego=10., lead=0., age=.1, bounds=BOUNDS, **kwargs):
  return assess(gap, ego, lead, age, bounds, in_path=kwargs.get('in_path', True), confirmed=kwargs.get('confirmed', True))


def test_stationary_obstacle_matches_stopping_distance_equation():
  v, a, delay, brake = 10., 1., .6, 4.
  expected = v * delay + .5 * a * delay ** 2 + (v + a * delay) ** 2 / (2 * brake)
  assert maximum_closure(v, 0., delay, brake, 8., a) == pytest.approx(expected)


def test_transient_collision_is_not_hidden_by_safe_final_positions():
  # Ego stops before the lead's final stop, but briefly overtakes the lead's
  # original rear position. Final stopping-distance subtraction misses this.
  closure = maximum_closure(10., 8., 0., 4., 1.)
  assert closure == pytest.approx(2/3)
  assert 10**2/(2*4) - 8**2/(2*1) < 0


def test_analytical_extrema_match_independent_dense_trajectories():
  rng = np.random.default_rng(90210)
  for _ in range(60):
    ego, lead = rng.uniform(0, 30, 2)
    brake, other = rng.uniform(.5, 10, 2)
    delay, accel = rng.uniform(0, 2, 2)
    end = max(delay + (ego + accel * delay)/brake, lead/other)
    t = np.linspace(0, end, 20001)
    response = np.minimum(t, delay)
    braking = np.clip(t-delay, 0, (ego+accel*delay)/brake)
    lead_time = np.minimum(t, lead/other)
    relative = ego*response + .5*accel*response**2 + (ego+accel*delay)*braking - .5*brake*braking**2
    relative -= lead*lead_time - .5*other*lead_time**2
    exact = maximum_closure(ego, lead, delay, brake, other, accel)
    assert exact >= np.max(relative) - 1e-10
    assert exact == pytest.approx(np.max(relative), abs=2e-4)


def test_protection_overrides_comfort_before_available_braking_is_exhausted():
  result = check()
  assert result['state'] == 'protective'
  assert BOUNDS.comfort_brake < result['required_brake'] < BOUNDS.ego_brake
  assert result['minimum_gap_at_limit'] > BOUNDS.clearance


def test_insufficient_distance_requests_limit_without_claiming_avoidance():
  result = check(gap=10.)
  assert result['maximum_brake_requested']
  assert result['required_brake'] == BOUNDS.ego_brake
  assert result['minimum_gap_at_limit'] < 0
  assert not result['avoidance_within_assumptions']


def test_delay_age_less_grip_and_lead_braking_cannot_improve_the_margin():
  base = check()['minimum_gap_at_limit']
  for kwargs in ({'age': .15}, {'bounds': replace(BOUNDS, response_seconds=1.)},
                 {'bounds': replace(BOUNDS, ego_brake=2., comfort_brake=2.)},
                 {'bounds': replace(BOUNDS, acceleration_during_response=2.)}):
    assert check(**kwargs)['minimum_gap_at_limit'] < base
  assert check(lead=10.)['minimum_gap_at_limit'] < check(lead=10., bounds=replace(BOUNDS, lead_brake=2.))['minimum_gap_at_limit']


@pytest.mark.parametrize('kwargs', [{'age': .16}, {'age': -1.}, {'gap': math.nan}, {'ego': -1.}, {'lead': -1.},
                                    {'confirmed': False}, {'in_path': None}, {'bounds': replace(BOUNDS, ego_brake=0.)}])
def test_unknown_or_unsupported_states_never_claim_a_clear_envelope(kwargs):
  result = check(**kwargs)
  assert result['state'] == 'unknown'
  assert not result['avoidance_within_assumptions']


def test_adjacent_target_and_confirmed_stationary_hold_do_not_request_full_braking():
  assert check(gap=1., in_path=False)['state'] == 'outside_path'
  result = check(gap=1., ego=0., bounds=replace(BOUNDS, acceleration_during_response=0.))
  assert result['state'] == 'holding' and not result['maximum_brake_requested']
  assert motion(5., 1., 4.) == (.125, 0.)


def test_report_cannot_be_mistaken_for_runtime_or_vehicle_qualification():
  report = experiments()
  assert report['runtime_connected'] is report['vehicle_calibrated'] is False
  assert any(c['result']['state'] == 'mitigation' for c in report['cases'])
