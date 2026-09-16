"""Deterministic, off-device queue/rate model, not deployed safety enforcement."""

from collections import Counter, deque
from dataclasses import dataclass
import math

from openpilot.tools.volt_gateway.protocol import ProtocolError


class TokenBucket:
  def __init__(self, rate: float, capacity: int = 2):
    if not math.isfinite(rate) or rate <= 0 or capacity < 1:
      raise ValueError("bucket limits")
    self.rate, self.capacity = rate, capacity
    self.tokens, self.last = float(capacity), 0.

  def take(self, now: float) -> bool:
    if not math.isfinite(now) or now < self.last:
      raise ValueError("non-monotonic clock")
    self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
    self.last = now
    if self.tokens < 1:
      return False
    self.tokens -= 1
    return True


@dataclass
class Packet:
  frames: tuple[bytes, ...]
  expires: float
  control: bool
  indivisible: bool
  index: int = 0


class Scheduler:
  """Control prioritization with optional ISO-TP packet non-interleaving.

  A transmitted/failed attempt both consume one token. OEM CAN contention is
  external: busy=True grants no transmission; there is no 'transmit while idle'
  algorithm here. Dropped partial streams resynchronize at the next index zero.
  """
  def __init__(self, rate: float = 40, telemetry_rate: float = 20, queue_limit: int = 8):
    if not 1 <= queue_limit <= 32:
      raise ValueError("queue limit")
    self.total, self.telemetry = TokenBucket(rate), TokenBucket(telemetry_rate)
    self.control, self.data = deque(), deque()
    self.queue_limit, self.active = queue_limit, None
    self.counters = Counter()
    self.enabled = True
    self.last = 0.

  def offer(self, frames: list[bytes], now: float, *, control: bool, indivisible: bool = False) -> bool:
    if not math.isfinite(now) or now < self.last or not 1 <= len(frames) <= 74 or any(len(f) != 8 for f in frames):
      raise ProtocolError("invalid queued packet")
    if not self.enabled:
      return False
    queue = self.control if control else self.data
    if len(queue) >= self.queue_limit:
      self.counters['control_rejected' if control else 'telemetry_dropped'] += 1
      if control:
        return False
      queue.popleft()
    queue.append(Packet(tuple(frames), now + (10 if control else .25), control, indivisible))
    return True

  def disable(self):
    self.enabled = False
    self.control.clear()
    self.data.clear()
    self.active = None

  def step(self, now: float, *, busy: bool = False) -> tuple[bytes, bool] | None:
    if not math.isfinite(now) or now < self.last:
      raise ValueError("non-monotonic clock")
    self.last = now
    if not self.enabled:
      return None
    for queue in (self.control, self.data):
      while queue and queue[0].expires <= now:
        queue.popleft()
        self.counters['expired'] += 1
    if self.active and self.active.expires <= now:
      self.active = None
      self.counters['expired'] += 1
    if busy:
      return None
    packet = self.active
    if packet is None:
      queue = self.control if self.control else self.data
      if not queue:
        return None
      packet = queue[0]
    # Do not consume total tokens when the telemetry sub-budget cannot admit it.
    old = (self.telemetry.tokens, self.telemetry.last)
    if not packet.control and not self.telemetry.take(now):
      return None
    if not self.total.take(now):
      if not packet.control:
        self.telemetry.tokens, self.telemetry.last = old
      return None
    frame = packet.frames[packet.index]
    packet.index += 1
    queue = self.control if packet.control else self.data
    if self.active is None and (packet.indivisible or packet.index == len(packet.frames)):
      queue.popleft()
    if packet.indivisible:
      self.active = packet if packet.index < len(packet.frames) else None
    self.counters['attempts'] += 1
    self.counters['control_attempts' if packet.control else 'telemetry_attempts'] += 1
    return frame, packet.control
