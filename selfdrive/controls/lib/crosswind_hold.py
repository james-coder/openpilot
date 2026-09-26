"""Crosswind hold: allow the Volt's steering PID an integral term only while a real crosswind is reported.

Without an integral the angle PID holds a steady side push (strong crosswind) with a steady error, and the car
settles off-center in the lane. The integral fixes that, but it changes steering behaviour, so it is enabled
only while windd (system/wind_monitor.py) reports a fresh crosswind component of at least ON_MPH across the
car's GPS heading, with hysteresis. Outside that window the controller is exactly the stock P-only one.
Steering torque limits (3 Nm, panda-enforced) and driver override are unchanged.

The same decision drives the onroad HOLD label (selfdrive/ui/onroad/wind_overlay.py).
"""
import json
import math
import threading
import time
from pathlib import Path

ROOT = Path('/data/wind')
ON_MPH = 12.          # crosswind component that turns the hold on
OFF_MPH = 8.          # ... and off (hysteresis)
STATUS_MAX_AGE_S = 30.   # windd must be alive
READING_MAX_AGE_S = 1800.  # wind reading no older than 30 min
PARAM = 'VoltCrosswindHold'  # 'auto' (default) or 'off'


def crosswind_mph(status: dict, now: float) -> float | None:
  """|crosswind| in mph from a windd status dict, or None if windd or its reading is stale/unusable."""
  try:
    t, rt, cross = status['time'], status['reading_time'], status.get('cross_mph')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (t, rt, cross)):
      return None
    if not 0 <= now - t <= STATUS_MAX_AGE_S or not 0 <= now - rt <= READING_MAX_AGE_S:
      return None
    return abs(float(cross))
  except (KeyError, TypeError):
    return None


def next_active(cross: float | None, active: bool) -> bool:
  if cross is None:
    return False
  return cross >= OFF_MPH if active else cross >= ON_MPH


def read_status(root: Path = ROOT) -> dict:
  try:
    with (root / 'status.json').open() as f:
      status = json.loads(f.read(4097))
    return status if isinstance(status, dict) else {}
  except (OSError, ValueError):
    return {}


class CrosswindHold:
  """Background reader: `.active` is refreshed every POLL_S without any disk I/O on the caller's thread."""
  POLL_S = 2.

  def __init__(self, enabled: bool, root: Path = ROOT, start_thread: bool = True):
    self.enabled = enabled
    self.root = root
    self.active = False
    self.cross = None
    if enabled and start_thread:
      threading.Thread(target=self._poll, name='crosswind-hold', daemon=True).start()

  def refresh(self, now: float | None = None):
    now = time.time() if now is None else now  # noqa: TID251 -- windd writes wall-clock timestamps
    self.cross = crosswind_mph(read_status(self.root), now)
    self.active = self.enabled and next_active(self.cross, self.active)

  def _poll(self):
    while True:
      try:
        self.refresh()
      except Exception:
        self.active = False
      time.sleep(self.POLL_S)
