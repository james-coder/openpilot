"""Optional, read-only Aranet4 on-road display.

The render thread only reads a cached scalar. All filesystem work is done by a
daemon worker and failure of the cabin sensor never affects the driving UI.
"""
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

import pyray as rl

from openpilot.selfdrive.car.aranet import ROOT
from openpilot.selfdrive.ui.onroad.hud_renderer import FONT_SIZES, COLORS, UI_CONFIG
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


POLL_SECONDS = 5
STATUS_MAX_AGE = 20


def latest_co2(root: Path = ROOT, now: float | None = None) -> tuple[int, float] | None:
  """Return (ppm, expiry wall time), or None if collector/sample is unavailable."""
  now = time.time() if now is None else now  # noqa: TID251 -- history persists across reboots
  try:
    with (root / 'status.json').open() as status_file:
      status = json.loads(status_file.read(2049))
    status_time = status['time']
    if (status['state'] != 'recording' or type(status_time) not in (int, float) or
        not math.isfinite(status_time) or not 0 <= now - status_time <= STATUS_MAX_AGE):
      return None
    with sqlite3.connect(f'file:{root / "history.sqlite"}?mode=ro', uri=True, timeout=.05) as db:
      row = db.execute('SELECT t, co2, interval FROM readings ORDER BY t DESC LIMIT 1').fetchone()
    if row is None:
      return None
    timestamp, ppm, interval = row
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in row) or
        not 0 <= ppm <= 32767 or not 1 <= interval <= 3600):
      return None
    expiry = timestamp + min(600, max(180, 2 * interval))
    return (round(ppm), expiry) if timestamp <= now <= expiry else None
  except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
    return None


class Co2Overlay(Widget):
  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._latest: tuple[int, float] | None = None
    self._badge: rl.Rectangle | None = None
    try:
      threading.Thread(target=self._poll, name='co2-ui-reader', daemon=True).start()
    except RuntimeError:
      pass  # Optional display; no dependency on thread creation.

  def _poll(self):
    while True:
      try:
        self._latest = latest_co2()
      except Exception:
        self._latest = None  # A malformed optional source must not kill the UI.
      time.sleep(POLL_SECONDS)

  def _render(self, rect: rl.Rectangle):
    value = self._latest
    self._badge = None
    if value is None or time.time() > value[1]:  # noqa: TID251 -- persisted sample expiry
      return
    text = f'CO₂ {value[0]} ppm'
    size = FONT_SIZES.max_speed
    text_size = measure_text_cached(self._font, text, size)
    x = rect.x + rect.width - UI_CONFIG.border_size - text_size.x - 16
    y = rect.y + rect.height - UI_CONFIG.border_size - text_size.y - 20
    # On RHD cars the driver-monitoring icon occupies the bottom-right corner.
    from openpilot.selfdrive.ui.ui_state import ui_state
    if ui_state.sm['driverMonitoringState'].isRHD:
      y -= UI_CONFIG.button_size + 20
    backing = rl.Rectangle(x - 12, y - 8, text_size.x + 28, text_size.y + 16)
    self._badge = backing
    rl.draw_rectangle_rounded(backing, .2, 6, COLORS.BLACK_TRANSLUCENT)
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, COLORS.WHITE)

  def hit_test(self, pos) -> bool:
    return self._badge is not None and rl.check_collision_point_rec(pos, self._badge)

  def hide(self):
    self._badge = None
