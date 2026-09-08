"""Finite, radar-anchored stopping references for an explicitly selected Volt profile.

Only the comfort objective uses these references. MPC collision constraints and
actuator authority remain independent. No fitting or file I/O runs in this loop.
"""

import numpy as np


def validate_curve(curve):
  from openpilot.selfdrive.controls.lib.volt_polynomial import valid_model
  if isinstance(curve, dict) and 'model' in curve:
    return valid_model(curve)
  try:
    speed, decel = np.asarray(curve['speed']), np.asarray(curve['deceleration'])
    knots, coefficients = np.asarray(curve['knots']), np.asarray(curve['coefficients'])
    if (speed.ndim != 1 or speed.shape != decel.shape or len(speed) < 2 or speed[0] != 0 or not np.all(np.diff(speed) > 0)
        or not np.isfinite(speed).all() or not np.isfinite(decel).all() or not np.all((decel >= .1) & (decel <= 2.5))
        or not 4.5 <= curve['gap'] <= 8 or knots.shape != (7,) or coefficients.shape != (6, 4)
        or knots[0] != 0 or not np.all(np.diff(knots) > 0) or not .5 <= knots[-1] <= 20
        or abs(speed[-1] - knots[-1]) > 1e-6
        or not np.isfinite(knots).all() or not np.isfinite(coefficients).all()):
      return False
    previous = 0.
    for i in range(6):
      values = np.polyval(coefficients[i], np.linspace(0., knots[i+1] - knots[i], 101))
      if abs(values[0] - previous) > 1e-5 or min(values) < -1e-6 or np.min(np.diff(values)) < -1e-6:
        return False
      previous = values[-1]
    return True
  except (KeyError, TypeError, ValueError, IndexError):
    return False


class StopTrajectory:
  def __new__(cls, curve):
    if 'model' in curve:
      from openpilot.selfdrive.controls.lib.volt_polynomial import PolynomialStopTrajectory
      return PolynomialStopTrajectory(curve)
    return super().__new__(cls)

  def __init__(self, curve):
    self.curve = curve
    self.speed = [float(v) for v in curve['speed']]
    self.decel = [float(v) for v in curve['deceleration']]
    self.slopes = [(b-a)/(vb-va) for va, vb, a, b in zip(self.speed, self.speed[1:], self.decel, self.decel[1:], strict=False)]
    self.reset()

  def reset(self):
    self.reference = None
    self.elapsed = self.travel = 0.
    self.anchor = None
    self.target_shift = 0.
    self.retry_in = 0.
    self.reason = 'inactive'

  def _braking(self, speed, acceleration, scale):
    dt = .05  # Same cadence as the planner; interpolation supplies its horizon nodes.
    v, a, x = max(0., speed), min(0., acceleration), 0.
    interval = len(self.speed) - 2
    result = [(0., x, v, a)]
    for i in range(800):
      while interval > 0 and v < self.speed[interval]:
        interval -= 1
      desired = -min(2.5, scale * (self.decel[interval] + self.slopes[interval] * (v - self.speed[interval])))
      a = max(a - 1.5 * dt, min(desired, a + 1.5 * dt))
      next_v = max(0., v + a * dt)
      x += .5 * (v + next_v) * dt
      v = next_v
      result.append(((i + 1) * dt, x, v, a if v > 0 else 0.))
      if v == 0:
        return np.asarray(result)
    return None

  def start(self, speed, acceleration, distance):
    self.reset()
    if not .3 < speed <= self.curve['speed'][-1] or distance <= self.curve['gap']:
      self.reason = 'outside support'
      return False
    remaining = distance - self.curve['gap']
    reference = self._braking(speed, acceleration, 1.)
    if reference is None:
      return False
    # Compress the approach only within the existing comfort deceleration bound.
    # Infeasible stops go back to the normal collision-aware planner.
    if reference[-1, 1] > remaining:
      # Distance is approximately inverse in deceleration scale. A bounded
      # correction avoids running an iterative optimizer on the radar core.
      scale = 1.
      for _ in range(3):
        scale = min(8., scale * reference[-1, 1] / remaining * 1.02)
        reference = self._braking(speed, acceleration, scale)
        if reference is None or reference[-1, 1] <= remaining:
          break
      if reference is None or reference[-1, 1] > remaining:
        self.reason = 'insufficient stopping distance'
        return False
    coast = max(0., (remaining - reference[-1, 1]) / speed)
    reference[:, 0] += coast
    reference[:, 1] += coast * speed
    self.reference = reference
    self.coast, self.initial_speed = coast, speed
    self.anchor = distance
    self.reason = 'tracking'
    return True

  def update(self, times, speed, acceleration, distance, dt):
    if self.retry_in > 0:
      self.retry_in -= dt
      return None
    if self.reference is None and not self.start(speed, acceleration, distance):
      self.retry_in = .5
      return None
    # A stationary radar target should remain in the same world location. Do not
    # silently move a latched stop endpoint to accommodate a changed obstacle.
    shift = distance + self.travel - self.anchor
    if abs(shift - self.target_shift) > 1.:
      self.reset()
      self.reason = 'lead position changed'
      return None
    # Follow small radar/odometry corrections without restarting the clock.
    # Larger inconsistencies invalidate the reference above.
    self.target_shift += float(np.clip((shift - self.target_shift) * dt / .25, -.5 * dt, .5 * dt))
    t = self.elapsed + np.asarray(times)
    r = self.reference
    x = np.where(t < self.coast, t * self.initial_speed, np.interp(t, r[:, 0], r[:, 1])) - self.travel + self.target_shift
    v = np.interp(t, r[:, 0], r[:, 2])
    a = np.interp(t, r[:, 0], r[:, 3], left=0.)
    self.elapsed += dt
    self.travel += max(0., speed) * dt
    return np.column_stack((x, v, a))

  @property
  def remaining_time(self):
    return max(0., self.reference[-1, 0] - self.elapsed) if self.reference is not None else None
