"""Synthetic actual-renderer preview; no device access or recorder writes."""
import sys
import time
from pathlib import Path

import pyray as rl

from openpilot.system.hardware.thermal_history import History, SENSORS
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.layouts.settings.thermal_history import ThermalHistoryLayout


def main():
  output = Path(sys.argv[1])
  output.mkdir(parents=True, exist_ok=True)
  rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
  gui_app.init_window('Thermal preview')
  layout = ThermalHistoryLayout()
  layout.last_poll = time.monotonic()+3600
  history = History()
  for now in range(0, 3601, 5):
    history.sample(now, 1789510000+now, 'offroad', dict.fromkeys(SENSORS, 76.7), dict(fan_percent=29, fan_rpm=2700))
  original_text = layout.text

  def checked_text(value, x, y, size=30, color=rl.WHITE):
    extent = measure_text_cached(layout.font, value, size)
    assert x+extent.x <= gui_app.width and y+extent.y <= gui_app.height, value
    original_text(value, x, y, size, color)

  layout.text = checked_text
  for name in ('history', 'empty'):
    layout.data = history.data if name == 'history' else None
    layout.message = 'Synthetic fixture' if name == 'history' else 'No saved history. Optional recorder may not be installed or running.'
    target = rl.load_render_texture(gui_app.width, gui_app.height)
    rl.begin_texture_mode(target)
    layout.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
    rl.end_texture_mode()
    capture = rl.load_image_from_texture(target.texture)
    rl.image_flip_vertical(capture)
    assert rl.export_image(capture, str(output / (name+'.png')))
    rl.unload_image(capture)
    rl.unload_render_texture(target)


if __name__ == '__main__':
  main()
