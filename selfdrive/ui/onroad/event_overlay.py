"""Silent "event saved" banner for the onroad view, fed by system/review/eventd.py.

Same contract as the wind, CO2 and check-engine badges: a daemon thread reads /data/review/last_event.json, the render
thread only reads the cached value, and nothing here can affect driving. No sound and no alert: it raises nothing in
selfdrived. The record carries the boot id and boot-clock time it was written at, so a file left over from an earlier
drive or boot is never shown.
"""
import json
import threading
import time
from pathlib import Path

import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import FONT_SIZES, COLORS, UI_CONFIG
from openpilot.system.review.banner import banner_text, current_boot_id
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

LAST_EVENT = Path('/data/review/last_event.json')
POLL_SECONDS = 0.5
BANNER_COLOR = rl.Color(255, 176, 40, 255)


class EventOverlay(Widget):
  _shared_record = None
  _reader_thread: threading.Thread | None = None

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._boot = current_boot_id()
    if self.__class__._reader_thread is None or not self.__class__._reader_thread.is_alive():
      try:
        worker = threading.Thread(target=self._poll, name='event-ui-reader', daemon=True)
        worker.start()
        self.__class__._reader_thread = worker
      except RuntimeError:
        pass

  def _poll(self):
    while True:
      try:
        with LAST_EVENT.open() as f:
          self.__class__._shared_record = json.loads(f.read(4097))
      except (OSError, ValueError):
        self.__class__._shared_record = None
      time.sleep(POLL_SECONDS)

  def _render(self, rect: rl.Rectangle):
    text = banner_text(self.__class__._shared_record, self._boot, time.clock_gettime(time.CLOCK_BOOTTIME))
    if text is None:
      return
    size = FONT_SIZES.max_speed
    text_size = measure_text_cached(self._font, text, size)
    x = rect.x + (rect.width - text_size.x) / 2
    y = rect.y + UI_CONFIG.border_size + 120
    rl.draw_rectangle_rounded(rl.Rectangle(x - 20, y - 10, text_size.x + 40, text_size.y + 20), .25, 6, COLORS.BLACK_TRANSLUCENT)
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, BANNER_COLOR)

  def hit_test(self, pos) -> bool:
    return False
