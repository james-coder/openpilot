"""Optional wind badge for the onroad view, fed by system/wind_monitor.py (windd).

Same contract as co2_overlay: the render thread only reads a cached value that a daemon
thread refreshes from /data/wind/status.json; nothing here can affect driving. ASCII only,
the device font has no arrows, so the arrow is drawn as a triangle.
"""
import json
import math
from pathlib import Path
import threading
import time

import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import FONT_SIZES, COLORS, UI_CONFIG
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

ROOT = Path('/data/wind')
POLL_SECONDS = 5
STATUS_MAX_AGE_S = 30      # daemon must have written within this
READING_MAX_AGE_S = 6 * 3600  # hide only when the last reading is this old (long offline stretch)
READING_OLD_S = 1200          # dim past this and show the age, so a stale value is never mistaken for live
STRONG_MPH = 25
STRONG_COLOR = rl.Color(255, 170, 40, 255)
DIM = rl.Color(255, 255, 255, 140)
COMPASS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


def compass(deg):
  return COMPASS[int((deg % 360) / 45 + .5) % 8]


def relative_to_heading(wind_from_deg, heading_deg):
  """Screen angle (deg clockwise from straight ahead) of the direction the wind blows TO,
  relative to the car's heading. 0 = tailwind pushing forward, 180 = headwind, 90 = from the left."""
  return (wind_from_deg + 180. - heading_deg) % 360.


def latest_wind(root: Path = ROOT, now: float | None = None):
  """(wind_mph, gust_mph|None, dir_deg, age_s) or None if the daemon or reading is unusable."""
  now = time.time() if now is None else now  # noqa: TID251 -- persisted wall timestamps
  try:
    with (root / 'status.json').open() as f:
      status = json.loads(f.read(4097))
    t = status['time']
    if type(t) not in (int, float) or not math.isfinite(t) or not 0 <= now - t <= STATUS_MAX_AGE_S:
      return None
    r = status.get('reading')
    if not isinstance(r, dict):
      return None
    speed, gust, direction, rt = r.get('wind_mph'), r.get('gust_mph'), r.get('dir_deg'), status.get('reading_time')
    for v in (speed, direction, rt):
      if type(v) not in (int, float) or not math.isfinite(v):
        return None
    if not (0 <= speed <= 250) or not (0 <= direction < 360) or not 0 <= now - rt <= READING_MAX_AGE_S:
      return None
    if type(gust) not in (int, float) or not math.isfinite(gust) or not 0 <= gust <= 300:
      gust = None
    return float(speed), gust, float(direction), now - rt
  except (OSError, ValueError, TypeError, KeyError):
    return None


def left_badge_x(rect: rl.Rectangle) -> float:
  """Left edge for the bottom-left badges (wind, check engine): right of the driver-monitoring
  face icon, which sits in the bottom-left corner of a left-hand-drive car."""
  rhd = False
  try:
    from openpilot.selfdrive.ui.ui_state import ui_state
    rhd = ui_state.started and ui_state.sm['driverMonitoringState'].isRHD
  except Exception:
    pass
  return rect.x + UI_CONFIG.border_size + (16 if rhd else UI_CONFIG.button_size + 24)


def _heading():
  """Car heading in degrees from the UI's GPS feed, or None."""
  try:
    from openpilot.selfdrive.ui.ui_state import ui_state
    sm = ui_state.sm
    for svc in ('gpsLocation', 'gpsLocationExternal'):
      if svc in sm.services and sm.alive[svc] and sm.valid[svc]:
        g = sm[svc]
        if g.hasFix and g.speed > 1.5:  # bearing is meaningless when nearly stopped
          return float(g.bearingDeg)
  except Exception:
    pass
  return None


class WindOverlay(Widget):
  _shared_latest = None
  _reader_thread: threading.Thread | None = None

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._badge: rl.Rectangle | None = None
    if self.__class__._reader_thread is None or not self.__class__._reader_thread.is_alive():
      try:
        worker = threading.Thread(target=self._poll, name='wind-ui-reader', daemon=True)
        worker.start()
        self.__class__._reader_thread = worker
      except RuntimeError:
        pass

  def _poll(self):
    while True:
      try:
        self.__class__._shared_latest = latest_wind()
      except Exception:
        self.__class__._shared_latest = None
      time.sleep(POLL_SECONDS)

  def _render(self, rect: rl.Rectangle):
    value = self.__class__._shared_latest
    self._badge = None
    if value is None:
      return
    speed, gust, direction, age = value
    text = f'WIND {speed:.0f} mph {compass(direction)}'
    if gust is not None and gust >= speed + 8:
      text += f' G{gust:.0f}'
    if age > READING_OLD_S:
      text += f' ({age / 60:.0f}m ago)'
    size = FONT_SIZES.max_speed
    text_size = measure_text_cached(self._font, text, size)
    arrow = size * 1.1
    # bottom-left, beside the driver-monitoring icon; the CO2 badge (if any) is bottom-right
    x = left_badge_x(rect)
    y = rect.y + rect.height - UI_CONFIG.border_size - text_size.y - 20
    backing = rl.Rectangle(x - 12, y - 8, text_size.x + arrow + 40, text_size.y + 16)
    self._badge = backing
    rl.draw_rectangle_rounded(backing, .2, 6, COLORS.BLACK_TRANSLUCENT)
    color = STRONG_COLOR if speed >= STRONG_MPH or (gust or 0) >= STRONG_MPH + 10 else COLORS.WHITE
    if age > READING_OLD_S:
      color = DIM
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, color)
    heading = _heading()
    if heading is not None:
      self._draw_arrow(x + text_size.x + 14 + arrow / 2, y + text_size.y / 2, arrow / 2,
                       relative_to_heading(direction, heading), color)

  @staticmethod
  def _draw_arrow(cx, cy, r, angle_deg, color):
    """Triangle pointing in the direction the wind blows, relative to the car (up = forward)."""
    a = math.radians(angle_deg)
    tip = rl.Vector2(cx + r * math.sin(a), cy - r * math.cos(a))
    left = rl.Vector2(cx + r * math.sin(a + 2.5), cy - r * math.cos(a + 2.5))
    right = rl.Vector2(cx + r * math.sin(a - 2.5), cy - r * math.cos(a - 2.5))
    rl.draw_triangle(tip, left, right, color)
    rl.draw_triangle(tip, right, left, color)  # both windings so it fills regardless of orientation

  def hit_test(self, pos) -> bool:
    return False

  def hide(self):
    self._badge = None
