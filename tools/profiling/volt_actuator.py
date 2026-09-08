"""Host-only causal actuator integration, shared by identification and replay."""

from collections import deque
import math


def lag_update(value, target, dt, rise, release):
  tau = rise if target > value else release
  return value + (-math.expm1(-dt / tau)) * (target - value)


class DelayedResponse:
  """Exact first-order response between timestamped command changes.

  Commands apply at the start of a step; the returned state is at its end.
  Delayed changes within a step split the integration interval, without rounding
  delay to a sample count. Synthetic runs start with known zero command/state.
  Recorded runs must explicitly seed both state and preceding command history.
  """
  def __init__(self, delay, rise, release=None, initial=0.):
    self.delay, self.rise = float(delay), float(rise)
    self.release = float(release if release is not None else rise)
    if self.delay < 0 or min(self.rise, self.release) <= 0:
      raise ValueError('Invalid actuator timing')
    self.time, self.command, self.value = 0., 0., float(initial)
    self.pending = deque()

  def seed(self, time, history, value):
    history = sorted(history)
    preceding = [(t, v) for t, v in history if t + self.delay <= time + 1e-9]
    if not preceding:
      raise ValueError('Insufficient preceding actuator command history')
    self.time, self.value = float(time), float(value)
    self.command = float(preceding[-1][1])
    self.pending = deque((float(t + self.delay), float(v)) for t, v in history
                         if t <= time and t + self.delay > time + 1e-9)

  def step(self, command, dt, transform=None):
    if not math.isfinite(command) or not math.isfinite(dt) or dt <= 0:
      raise ValueError('Unknown actuator input or invalid timestep')
    self.pending.append((self.time + self.delay, float(command)))
    end = self.time + dt
    while self.time < end - 1e-12:
      while self.pending and self.pending[0][0] <= self.time + 1e-10:
        _, self.command = self.pending.popleft()
      boundary = min(end, self.pending[0][0]) if self.pending else end
      target = transform(self.command) if transform else self.command
      self.value = lag_update(self.value, target, boundary - self.time, self.rise, self.release)
      self.time = boundary
    self.time = end
    return self.value


def friction_motion(speed, drive_accel, brake_capacity, dt):
  """Signed motion with static friction and an exact within-step stop crossing.

  Forces are expressed as acceleration equivalents. Brake capacity cannot propel
  the car backwards; gravity/creep can, if available friction cannot hold it.
  Returns end speed, displacement and actual mean kinematic acceleration.
  """
  if not all(math.isfinite(x) for x in (speed, drive_accel, brake_capacity, dt)) or brake_capacity < 0 or dt <= 0:
    raise ValueError('Invalid contact state')
  def rest_accel():
    return math.copysign(max(0., abs(drive_accel) - brake_capacity), drive_accel)
  accel = drive_accel - math.copysign(brake_capacity, speed) if speed != 0 else rest_accel()
  end = speed + accel * dt
  if speed != 0 and speed * end <= 0:
    stopped_at = -speed / accel
    remaining = dt - stopped_at
    end = rest_accel() * remaining
    distance = speed * stopped_at / 2 + end * remaining / 2
  else:
    distance = (speed + end) * dt / 2
  return end, distance, (end - speed) / dt
