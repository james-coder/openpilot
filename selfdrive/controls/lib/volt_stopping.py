"""Volt-only, opt-in feedback through regeneration fade and stationary hold."""

import numpy as np
from opendbc.car.gm.volt_longitudinal import PROFILE
from openpilot.common.realtime import DT_CTRL


class VoltStopping:
  def __init__(self, profile=PROFILE):
    self.profile = profile
    self.target = 0.0
    self.stopped_time = 0.0

  def reset(self):
    self.target = 0.0
    self.stopped_time = 0.0

  def update(self, CS, planned_accel, entering, last_output, pid):
    profile = self.profile
    desired = -float(np.interp(max(0.0, CS.vEgo), profile.stop_speed, profile.stop_decel))
    # Keep urgent deceleration; comfort must never limit an emergency request.
    desired = min(desired, planned_accel)
    self.stopped_time = self.stopped_time + DT_CTRL if CS.standstill else 0.0
    if entering:
      self.target = min(0.0, CS.aEgo)
      pid.i = float(np.clip(last_output - self.target, -profile.integral_limit, profile.integral_limit))
    if self.stopped_time >= 0.2:
      # The allocator preserves stock holding force. Do not integrate noisy
      # standstill acceleration and slowly increase brake pressure indefinitely.
      pid.reset()
      self.target = -0.85
      return self.target
    # Taper toward zero continuously while moving. Stronger planner requests
    # bypass the comfort slew, retaining the existing deceleration authority.
    self.target = min(planned_accel, float(np.clip(desired, self.target - 0.8 * DT_CTRL, self.target + 0.8 * DT_CTRL)))
    pid.i = float(np.clip(pid.i, -profile.integral_limit, profile.integral_limit))
    pid.update(self.target - CS.aEgo, speed=CS.vEgo, feedforward=self.target)
    pid.i = float(np.clip(pid.i, -profile.integral_limit, profile.integral_limit))
    return min(0.0, float(np.clip(pid.p + pid.i + pid.f, pid.neg_limit, pid.pos_limit)))
