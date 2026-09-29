"""Block counter for the bounded steering experiment (selfdrive/controls/lib/steer_experiment.py).

Shows "TEST 3/10" only while an experiment is armed and running (and "TEST DONE" when finished). It never shows
which mode is active: the driver is blind to A/B. Same contract as the other badges: a daemon thread reads the
status file, the render thread only reads the cached text, and nothing here can affect driving. ASCII only.
"""
import json
import math
from pathlib import Path
import threading
import time

import pyray as rl

from openpilot.selfdrive.ui.onroad.hud_renderer import FONT_SIZES, COLORS, UI_CONFIG
from openpilot.selfdrive.ui.onroad.wind_overlay import left_badge_x
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

ROOT = Path('/data/steer_exp')
POLL_SECONDS = 1
STATUS_MAX_AGE_S = 5
DIM = rl.Color(255, 255, 255, 140)


def status_text(root: Path = ROOT, now: float | None = None) -> str | None:
  now = time.time() if now is None else now  # noqa: TID251 -- status.json is stamped with wall time
  try:
    with (root / 'status.json').open() as f:
      s = json.loads(f.read(2049))
    t, block, of = s['time'], s['block'], s['of']
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (t, block, of)):
      return None
    if not 0 <= now - t <= STATUS_MAX_AGE_S or not 1 <= block <= of <= 20:
      return None
    if s.get('state') == 'done':
      return 'TEST DONE'
    if s.get('state') != 'running':
      return None
    return f'TEST {int(block)}/{int(of)}' + ('' if s.get('qualifying') else ' (paused)')
  except (OSError, ValueError, TypeError, KeyError):
    return None


class SteerTestOverlay(Widget):
  _shared_text: str | None = None
  _reader_thread: threading.Thread | None = None

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    if self.__class__._reader_thread is None or not self.__class__._reader_thread.is_alive():
      try:
        worker = threading.Thread(target=self._poll, name='steer-test-ui-reader', daemon=True)
        worker.start()
        self.__class__._reader_thread = worker
      except RuntimeError:
        pass

  def _poll(self):
    while True:
      try:
        self.__class__._shared_text = status_text()
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
    # third row up, above the check-engine badge (which sits above the wind badge)
    y = rect.y + rect.height - UI_CONFIG.border_size - 3 * text_size.y - 80
    rl.draw_rectangle_rounded(rl.Rectangle(x - 12, y - 8, text_size.x + 28, text_size.y + 16), .2, 6, COLORS.BLACK_TRANSLUCENT)
    rl.draw_text_ex(self._font, text, rl.Vector2(x, y), size, 0, DIM if '(paused)' in text else COLORS.WHITE)

  def hit_test(self, pos) -> bool:
    return False
