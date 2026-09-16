"""Pure, monotonic LTE retry policy. No I/O, hardware reset or driving dependency."""
from dataclasses import dataclass


@dataclass
class RetryPolicy:
  failures: int = 0
  next_attempt: float = 0.0
  stable_since: float | None = None
  hangups: int = 0
  first_hangup: float | None = None
  reason: str = "none"

  def failed(self, now: float, reason: str):
    self.failures += 1
    self.reason = reason
    self.stable_since = None
    self.next_attempt = now + (5, 15, 60)[min(self.failures - 1, 2)]
    if reason == "registered_early_hangup":
      if self.first_hangup is None:
        self.first_hangup = now
      self.hangups += 1
    else:
      self.clear_hangups()

  def clear_hangups(self):
    self.hangups = 0
    self.first_hangup = None

  def ready(self, now: float) -> bool:
    return now >= self.next_attempt

  def stable(self, now: float):
    if self.stable_since is None:
      self.stable_since = now
    if now - self.stable_since >= 300:
      self.failures = 0
      self.reason = "none"
      self.clear_hangups()

  def recovery_due(self, now: float) -> bool:
    return self.hangups >= 3 and self.first_hangup is not None and now - self.first_hangup >= 60


class PPPProgress:
  """Extract only fixed PPP phase markers; never retain/log raw peer text.

  A full overlong line is discarded, including its tail. Markers are diagnostic
  evidence, not authentication of the modem or authorization for a reset.
  """
  def __init__(self):
    self.pending = b""
    self.discarding = False
    self.authenticated = False
    self.address_assigned = False

  def feed(self, data: bytes):
    for part in data.splitlines(keepends=True):
      ended = part.endswith(b'\n')
      if not self.discarding:
        if len(self.pending) + len(part) > 512:
          self.pending = b""
          self.discarding = True
        else:
          self.pending += part
          if ended:
            line = self.pending.strip()
            self.authenticated |= line == b'CHAP authentication succeeded' or line == b'PAP authentication succeeded'
            self.address_assigned |= line.startswith(b'local  IP address ')
            self.pending = b""
      if ended:
        self.discarding = False
