"""Volt-only, opt-in feedback through regeneration fade and stationary hold."""

import numpy as np
from opendbc.car.gm.volt_longitudinal import PROFILE, VoltHold
from openpilot.common.realtime import DT_CTRL


class VoltStopLatch:
  """Finish an initiated stop only while the same fresh stationary lead remains."""
  def __init__(self, dt):
    self.dt = dt
    self.track = None
    self.stable = 0.
    self.holding = False

  def update(self, lead, secondary, age, initiated, speed, standstill, active, threshold):
    valid = (active and 0 <= age <= .3 and lead.status and lead.radar and lead.radarTrackId >= 0
             and lead.modelProb >= .9 and abs(lead.vLead) < .5
             and (not secondary.status or secondary.dRel >= lead.dRel))
    if not valid or self.track != lead.radarTrackId:
      self.track = lead.radarTrackId if valid else None
      self.stable = 0.
      self.holding = False
    if valid:
      self.stable += self.dt
      if self.stable >= .5 and (standstill or initiated and speed < threshold):
        self.holding = True
    return self.holding


class VoltStopping:
  def __init__(self, profile=PROFILE):
    self.profile = profile
    self.target = 0.0
    self.stopped_time = 0.0
    self.hold = VoltHold()

  def reset(self):
    self.target = 0.0
    self.stopped_time = 0.0
    self.hold = VoltHold()

  def update(self, CS, planned_accel, entering, last_output, pid, trajectory_active=False):
    profile = self.profile
    positive_limit = profile.integral_limit * float(np.clip(CS.vEgo / 2., 0., 1.))
    taper_speed = max(0., CS.vEgo + min(0., CS.aEgo) * profile.response_horizon)
    desired = min(0., planned_accel) if trajectory_active else -float(np.interp(taper_speed, profile.stop_speed, profile.stop_decel))
    # Keep urgent deceleration; comfort must never limit an emergency request.
    desired = min(desired, planned_accel)
    self.stopped_time = self.stopped_time + DT_CTRL if CS.standstill else 0.0
    if entering:
      # Carry the actual output through the mode transition. Seeding from
      # measured acceleration can jump when pressure response is delayed.
      self.target = min(0.0, last_output)
      pid.reset()
    if not trajectory_active or entering:
      self.hold.elapsed = 0.
    confirmed = self.hold.update(CS.standstill, CS.vEgoRaw, True, DT_CTRL) if trajectory_active else False
    if (confirmed if trajectory_active else self.stopped_time >= 0.2):
      # The allocator preserves stock holding force. Do not integrate noisy
      # standstill acceleration and slowly increase brake pressure indefinitely.
      pid.reset()
      self.target = -0.85
      return self.target
    # Taper toward zero continuously while moving. Stronger planner requests
    # bypass the comfort slew, retaining the existing deceleration authority.
    self.target = min(planned_accel, float(np.clip(desired, self.target - 0.8 * DT_CTRL, self.target + 1.5 * DT_CTRL)))
    pid.i = float(np.clip(pid.i, -profile.integral_limit, positive_limit))
    pid.update(self.target - CS.aEgo, speed=CS.vEgo, feedforward=self.target)
    pid.i = float(np.clip(pid.i, -profile.integral_limit, positive_limit))
    return min(0.0, float(np.clip(pid.p + pid.i + pid.f, pid.neg_limit, pid.pos_limit)))
