"""Offline transfer admission model. Constants are experimental, NOT vehicle-approved.

Samples include all observed traffic, including our own successful transmissions.
No attempt is made to subtract our traffic or fill all apparently unused capacity.
The real CAN driver must bound hardware auto-retries and enforce its own TX limits.
"""

from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Conditions:
  stationary: bool = False
  offroad: bool = False
  power_stable: bool = False
  vehicle_awake: bool = False
  authenticated: bool = False
  compatible: bool = False
  errors: bool = True

  @property
  def allowed(self):
    return (self.stationary and self.offroad and self.power_stable and self.vehicle_awake
            and self.authenticated and self.compatible and not self.errors)


class AdaptiveBudget:
  """Fixed 20-ms samples, 100/300/1000-ms windows, two-frame maximum burst.

  Positive trend is projected 300 ms ahead. Above the target, halve the rate;
  above the stop threshold, stop. Resume requires one second of clean samples.
  Conditions must be refreshed each sample; admission expires after 30 ms.
  All request/FC/ACK/retry traffic must share the aggregate budget in deployment.
  """
  PERIOD = .020
  STALE = .030

  def __init__(self, hard_fps: float = 80, target: float = .65, stop: float = .75):
    if not math.isfinite(hard_fps) or not 0 < hard_fps <= 200 or not 0 < target < stop < 1:
      raise ValueError("invalid experimental limits")
    self.hard_fps, self.target, self.stop = hard_fps, target, stop
    self.samples = deque(maxlen=50)
    self.rate = self.tokens = 0.
    self.last_sample = None
    self.last_attempt = 0.
    self.clean = 0
    self.reason = 'not initialized'

  def _pause(self, reason):
    self.rate = self.tokens = 0.
    self.clean = 0
    self.reason = reason

  def sample(self, now: float, occupied_bits: int, conditions: Conditions):
    if not math.isfinite(now) or now < self.last_attempt or (self.last_sample is not None and now <= self.last_sample):
      self._pause('invalid clock')
      raise ValueError('invalid clock')
    if self.last_sample is not None and abs(now - self.last_sample - self.PERIOD) > .001:
      self.samples.clear()
      self._pause('measurement gap')
    self.last_sample = now
    if type(occupied_bits) is not int or not 0 <= occupied_bits <= 10_000:
      self.samples.clear()
      self._pause('invalid measurement')
      return
    self.samples.append(occupied_bits / 10_000)
    if not conditions.allowed:
      self._pause('conditions failed')
      return
    self.clean += 1
    if len(self.samples) < 50 or self.clean < 50:
      self.reason = 'collecting clean history'
      return
    values = list(self.samples)
    short, medium, long = (sum(values[-n:]) / n for n in (5, 15, 50))
    projected = short + max(0., short - medium)
    load = max(short, medium, long, projected)
    # Also catch a single-window burst instead of smoothing it away.
    if max(load, values[-1]) >= self.stop:
      self._pause('bus busy')
      return
    if load >= self.target:
      self.rate *= .5
      self.tokens = 0.
      self.reason = 'backoff'
      return
    # Conservative 135-bit accounting for an 8-byte standard CAN frame.
    spare_fps = max(0., (self.target - load) * 500_000 / 135)
    ceiling = min(self.hard_fps, spare_fps)
    self.rate = min(ceiling, self.rate + 5 * self.PERIOD)
    self.tokens = min(self.tokens, 2.)
    self.reason = 'adaptive'

  def take(self, now: float) -> bool:
    if not math.isfinite(now) or now < self.last_attempt:
      self._pause('invalid clock')
      raise ValueError('invalid clock')
    elapsed = now - self.last_attempt
    self.last_attempt = now
    if self.last_sample is None or not 0 <= now - self.last_sample <= self.STALE:
      self._pause('stale measurement')
      return False
    self.tokens = min(2., self.tokens + elapsed * self.rate)
    if self.rate <= 0 or self.tokens < 1:
      return False
    self.tokens -= 1
    return True


class ChunkTransfer:
  """Stop-and-wait accounting only; no flash, transport, or authentication code.

  Caller may acknowledge ONLY an already authenticated response bound to this
  image/session. A retry reuses the same offset/data; receiver must be idempotent.
  Timeout starts after the complete chunk was sent, not before paced delivery.
  """
  def __init__(self, size: int, chunk_size: int = 256, retry_limit: int = 3):
    if not 0 < size <= 2**20 or not 1 <= chunk_size <= 256 or not 0 <= retry_limit <= 5:
      raise ValueError('transfer bounds')
    self.size, self.chunk_size, self.retry_limit = size, chunk_size, retry_limit
    self.offset = self.retries = 0
    self.deadline = None
    self.failed = False
    self.last = 0.

  def _clock(self, now):
    if not math.isfinite(now) or now < self.last:
      self.failed = True
      raise ValueError('invalid clock')
    self.last = now

  def sent(self, now: float):
    self._clock(now)
    if self.failed or self.offset == self.size or self.deadline is not None:
      raise ValueError('not ready to send')
    self.deadline = now + 5.

  def acknowledge(self, next_offset: int, now: float) -> bool:
    self._clock(now)
    expected = min(self.size, self.offset + self.chunk_size)
    if self.failed or self.deadline is None or now >= self.deadline or next_offset != expected:
      return False
    self.offset = expected
    self.deadline = None
    self.retries = 0
    return True

  def timeout(self, now: float) -> bool:
    self._clock(now)
    if self.failed or self.deadline is None or now < self.deadline:
      return False
    self.deadline = None
    if self.retries >= self.retry_limit:
      self.failed = True
      return False
    self.retries += 1
    return True
