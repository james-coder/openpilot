"""Check-engine badge for the onroad view, fed by the saved scan (ObdLastScan) that card refreshes
on every shift into Park and once per ignition cycle (selfdrive/car/obd_scan_controller.py).

Shown only while the last scan read the MIL as on, so it appears and goes away with the dashboard
light. Codes left on file after the light goes out are the startup alert's job, not this badge's.
Same contract as the CO2 and wind badges: a daemon thread reads the param, the render thread
only reads the cached value, and nothing here can affect driving.
"""
import threading
import time

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.car.obd_scan import STALE_SCAN_S, format_age, mil_state
from openpilot.selfdrive.ui.onroad.hud_renderer import FONT_SIZES, COLORS, UI_CONFIG
from openpilot.selfdrive.ui.onroad.wind_overlay import left_badge_x
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

POLL_SECONDS = 5
MIL_COLOR = rl.Color(255, 170, 40, 255)
MAX_CODES = 3


def badge_text(report, now: float) -> str | None:
  """Badge text while the last scan read the MIL on, else None."""
  lamp, codes, scanned = mil_state(report)
  if not lamp:
    return None
  text = 'CHECK ENGINE'
  if codes:
    text += ' ' + ' '.join(codes[:MAX_CODES]) + (f' +{len(codes) - MAX_CODES}' if len(codes) > MAX_CODES else '')
  if scanned is not None and now - scanned > STALE_SCAN_S:
    text += f' ({format_age(now - scanned)} old)'
  return text


class CheckEngineOverlay(Widget):
  _shared_text: str | None = None
  _reader_thread: threading.Thread | None = None

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    if self.__class__._reader_thread is None or not self.__class__._reader_thread.is_alive():
      try:
        worker = threading.Thread(target=self._poll, name='check-engine-ui-reader', daemon=True)
        worker.start()
        self.__class__._reader_thread = worker
      except RuntimeError:
        pass

  def _poll(self):
    params = Params()
    while True:
      try:
        self.__class__._shared_text = badge_text(params.get('ObdLastScan'), time.time())  # noqa: TID251 -- scan wall timestamp
      except Exception:
        self.__class__._shared_text = None
      time.sleep(POLL_SECONDS)

  def _render(self, rect: rl.Rectangle):
    text = self.__class__._shared_text
    if text is None:
      return
    size = FONT_SIZES.max_speed
    text_size = measure_text_cached(self._font, text, size)
    x = left_badge_x(rect)
    # one row above the wind badge, which sits on the bottom row
    y = rect.y + rect.height - UI_CONFIG.border_size - 2 * text_size.y - 50
    rl.draw_rectangle_rounded(rl.Rectangle(x - 12, y - 8, text_size.x + 28, text_size.y + 16), .2, 6, COLORS.BLACK_TRANSLUCENT)
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, MIL_COLOR)

  def hit_test(self, pos) -> bool:
    return False
