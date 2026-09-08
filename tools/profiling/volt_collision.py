"""Offline longitudinal collision-envelope experiments, with no actuator access.

This is NOT an AEB controller. Inputs are a time-aligned, bumper-to-bumper
snapshot with externally established path association and response bounds.
The recorded comfort-response fit does not establish those emergency bounds.
See docs/VOLT_COLLISION_PROTECTION.md for the missing end-to-end work.
"""

from dataclasses import dataclass, asdict, replace
import math


@dataclass(frozen=True)
class Envelope:
  # All values are explicit experiment assumptions, not Volt calibration.
  response_seconds: float
  ego_brake: float  # Lower bound on achieved net deceleration after response.
  lead_brake: float  # Upper bound on the lead's deceleration, without reversal.
  acceleration_during_response: float
  clearance: float
  max_observation_age: float
  comfort_brake: float

  def valid(self):
    return (all(math.isfinite(v) for v in asdict(self).values())
            and 0 <= self.response_seconds <= 3 and .05 <= self.ego_brake <= 12
            and .05 <= self.lead_brake <= 12 and 0 <= self.acceleration_during_response <= 5
            and 0 < self.clearance <= 10 and 0 <= self.max_observation_age <= 1
            and 0 < self.comfort_brake <= self.ego_brake)


def motion(t, speed, brake, delay=0., acceleration=0.):
  """Position/velocity; the vehicle cannot brake itself into reverse."""
  before = min(t, delay)
  x = speed * before + .5 * acceleration * before ** 2
  v = speed + acceleration * before
  braking = min(max(0., t - delay), v / brake)
  return x + v * braking - .5 * brake * braking ** 2, max(0., v - brake * braking)


def maximum_closure(ego_speed, lead_speed, delay, ego_brake, lead_brake, acceleration=0.):
  """Exact maximum relative travel, including transient closure before either stop.

  End-of-stop distance subtraction alone can miss a collision before the lead
  finally stops. Relative velocity is affine between these breakpoints; its
  zero crossing and the interval endpoints contain every possible extremum.
  """
  values = (ego_speed, lead_speed, delay, ego_brake, lead_brake, acceleration)
  if (not all(math.isfinite(v) for v in values) or min(ego_speed, lead_speed, delay, acceleration) < 0
      or min(ego_brake, lead_brake) <= 0):
    raise ValueError('Finite forward-motion states and positive braking bounds are required')
  times = sorted({0., delay, delay + (ego_speed + acceleration * delay) / ego_brake, lead_speed / lead_brake})

  def relative(t):
    ex, ev = motion(t, ego_speed, ego_brake, delay, acceleration)
    lx, lv = motion(t, lead_speed, lead_brake)
    return ex - lx, ev - lv

  candidates = list(times)
  for start, end in zip(times[:-1], times[1:], strict=True):
    v_start, v_end = relative(start)[1], relative(end)[1]
    if v_start * v_end < 0:
      candidates.append(start + (end - start) * v_start / (v_start - v_end))
  return max(relative(t)[0] for t in candidates)


def assess(gap, ego_speed, lead_speed, age, bounds, *, in_path, confirmed):
  """Assess ONE explicitly associated target, independent of a comfort curve.

  'unknown' never means clear. This function cannot select targets, carry out a
  degraded stop, arbitrate driver input, or send its requested acceleration.
  """
  unknown = {'state': 'unknown', 'required_brake': None, 'minimum_gap_at_limit': None,
             'maximum_brake_requested': False, 'avoidance_within_assumptions': False}
  if (not bounds.valid() or not all(math.isfinite(v) for v in (gap, ego_speed, lead_speed, age))
      or min(ego_speed, lead_speed, age) < 0 or age > bounds.max_observation_age
      or not isinstance(in_path, bool) or not isinstance(confirmed, bool)):
    return unknown
  if not in_path:
    return {**unknown, 'state': 'outside_path'}
  if not confirmed:
    return unknown
  delay = bounds.response_seconds + age

  def remaining(brake):
    return gap - maximum_closure(ego_speed, lead_speed, delay, brake, bounds.lead_brake, bounds.acceleration_during_response)

  minimum = remaining(bounds.ego_brake)
  safe = minimum >= bounds.clearance
  if ego_speed == 0 and bounds.acceleration_during_response == 0:
    return {**unknown, 'state': 'holding' if gap >= bounds.clearance else 'insufficient_gap',
            'required_brake': 0., 'minimum_gap_at_limit': minimum, 'avoidance_within_assumptions': safe}
  if not safe:
    # Strongest assumed braking is still requested for mitigation. Do not label
    # an unavoidable/uncertain case a successful collision-avoidance result.
    return {**unknown, 'state': 'mitigation', 'required_brake': bounds.ego_brake,
            'minimum_gap_at_limit': minimum, 'maximum_brake_requested': True}
  low, high = .0001, bounds.ego_brake
  for _ in range(40):
    middle = (low + high) / 2
    if remaining(middle) >= bounds.clearance:
      high = middle
    else:
      low = middle
  return {'state': 'protective' if high > bounds.comfort_brake else 'within_comfort_envelope',
          'required_brake': high, 'minimum_gap_at_limit': minimum,
          'maximum_brake_requested': high >= bounds.ego_brake - 1e-6,
          'avoidance_within_assumptions': True}


def experiments():
  """Reproducible synthetic comparisons, explicitly separate from vehicle proof."""
  base = Envelope(.5, 4., 8., 1., .5, .15, 2.5)
  cases = [('Walking-speed approach', 3., 2., 0., base),
           ('Stationary vehicle, stronger than comfort needed', 24., 10., 0., base),
           ('Stationary vehicle, already outside braking envelope', 10., 10., 0., base),
           ('Lead suddenly brakes', 30., 15., 15., base),
           ('Reduced braking capability', 24., 10., 0., replace(base, ego_brake=2., comfort_brake=2.)),
           ('Longer response delay', 24., 10., 0., replace(base, response_seconds=1.)),
           ('Already stationary', 1., 0., 0., replace(base, acceleration_during_response=0.))]
  return {'version': 1, 'runtime_connected': False, 'vehicle_calibrated': False,
          'note': 'Offline analytical experiments. Positive clearance depends on assumed detection, geometry, delay and braking bounds; '
                  + 'these are not measured emergency performance and do not establish crash protection.',
          'cases': [dict(name=name, gap=gap, ego_speed=ego, lead_speed=lead, age=.1, assumptions=asdict(bounds),
                         result=assess(gap, ego, lead, .1, bounds, in_path=True, confirmed=True))
                    for name, gap, ego, lead, bounds in cases]}
