import time
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog


class ApiPoller:
  """Bounded retries for optional offroad UI data; cached values belong to callers."""

  def __init__(self, name: str, interval: float):
    self.name = name
    self.interval = interval
    self.delay = interval
    self.next_attempt = 0.0
    self.network = None
    self.failed = False

  def poll(self, fetch: Callable[[], None], network: int, now: float | None = None) -> None:
    realtime = now is None
    now = time.monotonic() if now is None else now
    if network != self.network:
      self.network = network
      self.next_attempt = now
      self.delay = self.interval
    if not network or now < self.next_attempt:
      return
    try:
      fetch()
    except Exception as e:
      if not self.failed:
        cloudlog.warning(f"{self.name} unavailable ({type(e).__name__}); retrying with backoff")
      self.failed = True
      self.next_attempt = (time.monotonic() if realtime else now) + self.delay
      self.delay = min(self.delay * 2, 300.0)
    else:
      if self.failed:
        cloudlog.info(f"{self.name} recovered")
      self.failed = False
      self.delay = self.interval
      self.next_attempt = (time.monotonic() if realtime else now) + self.interval
