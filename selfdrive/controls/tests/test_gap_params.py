"""Pure personality/gap-parameter tests — importable and runnable without the ACADOS
solver (gap_params.py has no casadi/acados import), unlike test_following_distance.py
and test_volt_protection.py which construct a real LongitudinalMpc/Maneuver."""
import pytest

from cereal import log
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.gap_params import (
  get_T_FOLLOW, get_safe_obstacle_distance, resolve_gap_params,
)
from opendbc.car.gm.volt_following import GapParams, VoltFollowingProfile


FITTED = VoltFollowingProfile(
  validated=True,
  aggressive=GapParams(1.0, 2.0, 4.5),
  standard=GapParams(1.2, 2.2, 5.0),
  relaxed=GapParams(1.6, 2.5, 6.5),
)


@pytest.mark.parametrize('personality', [
  log.LongitudinalPersonality.aggressive, log.LongitudinalPersonality.standard, log.LongitudinalPersonality.relaxed,
])
def test_resolve_gap_params_stock_matches_get_t_follow(personality):
  # following_profile=None must reproduce today's stock lookup exactly, for every car.
  t_follow, comfort_brake, stop_distance = resolve_gap_params(personality, None, 6.0, 2.5)
  assert t_follow == get_T_FOLLOW(personality)
  assert (comfort_brake, stop_distance) == (2.5, 6.0)


def test_resolve_gap_params_with_profile_overrides_all_three():
  t_follow, comfort_brake, stop_distance = resolve_gap_params(log.LongitudinalPersonality.aggressive, FITTED, 6.0, 2.5)
  assert (t_follow, comfort_brake, stop_distance) == (1.0, 2.0, 4.5)
  # Constructor-supplied stop_distance/comfort_brake (from the braking PROFILE, unrelated
  # feature) must be ignored once a following_profile is present — no cross-contamination.
  t_follow2, comfort_brake2, stop_distance2 = resolve_gap_params(log.LongitudinalPersonality.aggressive, FITTED, 8.0, 3.0)
  assert (t_follow2, comfort_brake2, stop_distance2) == (1.0, 2.0, 4.5)


def test_safe_obstacle_distance_grows_with_speed_and_gap_params():
  low = get_safe_obstacle_distance(5.0, 1.25, 4.5, 2.0)
  high = get_safe_obstacle_distance(25.0, 1.25, 4.5, 2.0)
  assert high > low
  farther = get_safe_obstacle_distance(5.0, 1.75, 6.5, 2.5)
  assert farther > low
