import pytest
import itertools
from openpilot.common.parameterized import parameterized_class

from cereal import log

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import ACCEL_MIN, COMFORT_BRAKE, get_safe_obstacle_distance, \
  get_stopped_equivalence_factor, get_T_FOLLOW
from openpilot.selfdrive.test.longitudinal_maneuvers.maneuver import Maneuver


def desired_follow_distance(v_ego, v_lead, t_follow=None):
  if t_follow is None:
    t_follow = get_T_FOLLOW()
  return get_safe_obstacle_distance(v_ego, t_follow) - get_stopped_equivalence_factor(v_lead)

def run_following_distance_simulation(v_lead, t_end=100.0, e2e=False, personality=0):
  man = Maneuver(
    '',
    duration=t_end,
    initial_speed=float(v_lead),
    lead_relevancy=True,
    initial_distance_lead=100,
    speed_lead_values=[v_lead],
    breakpoints=[0.],
    e2e=e2e,
    personality=personality,
  )
  valid, output = man.evaluate()
  assert valid
  return output[-1,2] - output[-1,1]


@parameterized_class(("e2e", "personality", "speed"), itertools.product(
                      [True, False], # e2e
                      [log.LongitudinalPersonality.relaxed, # personality
                       log.LongitudinalPersonality.standard,
                       log.LongitudinalPersonality.aggressive],
                      [0,10,35])) # speed
class TestFollowingDistance:
  def test_following_distance(self):
    v_lead = float(self.speed)
    simulation_steady_state = run_following_distance_simulation(v_lead, e2e=self.e2e, personality=self.personality)
    correct_steady_state = desired_follow_distance(v_lead, v_lead, get_T_FOLLOW(self.personality))
    err_ratio = 0.2 if self.e2e else 0.1
    abs_err_margin = 0.5 if v_lead > 0.0 else 1.15
    assert simulation_steady_state == pytest.approx(correct_steady_state, abs=err_ratio * correct_steady_state + abs_err_margin)


def test_comfort_brake_is_commandable():
  # The planner must not plan on braking harder than it can command; 6.0 did, and braked late
  # and hard when closing on slower traffic (docs/2026-09-25-panic-brake-analysis.md).
  assert COMFORT_BRAKE <= -ACCEL_MIN


def test_stopped_car_from_highway_speed_stays_within_limits():
  man = Maneuver('', duration=25., initial_speed=27., lead_relevancy=True, initial_distance_lead=150.,
                 speed_lead_values=[0.], breakpoints=[0.], personality=log.LongitudinalPersonality.aggressive)
  valid, output = man.evaluate()
  assert valid
  assert output[:, 5].min() > ACCEL_MIN + .1  # 6.0 saturated at ACCEL_MIN here


@pytest.mark.parametrize("personality", [log.LongitudinalPersonality.relaxed, log.LongitudinalPersonality.aggressive])
@pytest.mark.parametrize("lead_stop_time", [10., 6.66])  # lead brakes from 20 m/s to a stop at 2 and 3 m/s^2
def test_keeps_gap_when_lead_brakes_hard(personality, lead_stop_time):
  # Stock maneuvers that failed with the short 09-17 following times until the planner was made
  # to respond promptly while the lead brakes (LEAD_BRAKING_JERK_SCALE).
  man = Maneuver('', duration=50., initial_speed=20., lead_relevancy=True, initial_distance_lead=35.,
                 speed_lead_values=[20., 20., 0.], breakpoints=[0., 15., 15. + lead_stop_time], personality=personality)
  valid, output = man.evaluate()
  assert valid
  assert output[:, 6].min() > .4
