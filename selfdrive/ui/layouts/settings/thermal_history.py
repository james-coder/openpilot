"""Bounded offroad thermal summary screen; no hardware control."""
import datetime
import time

import pyray as rl

from openpilot.system.hardware.thermal_history import ROOT, SENSORS, THRESHOLDS, read_summary
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.ui_state import ui_state


def date_label(value):
  try:
    return datetime.datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M') if value else 'date unavailable'
  except (ValueError, TypeError, OverflowError, OSError):
    return 'date unavailable'


class ThermalHistoryLayout(Widget):
  def __init__(self):
    super().__init__()
    self.font = gui_app.font(FontWeight.MEDIUM)
    self.mode = 'offroad'
    self.sensor = 0
    self.data = None
    self.last_poll = 0
    self.message = 'Waiting for history'
    self.buttons = []

  def _handle_mouse_release(self, pos):
    for rect, action in self.buttons:
      if rl.check_collision_point_rec(pos, rect):
        if action == 'Close':
          gui_app.pop_widget()
        elif action == 'Mode':
          self.mode = 'onroad' if self.mode == 'offroad' else 'offroad'
        else:
          self.sensor = (self.sensor + 1) % len(SENSORS)

  def text(self, value, x, y, size=30, color=rl.WHITE):
    rl.draw_text_ex(self.font, value, rl.Vector2(x, y), size, 0, color)

  def _render(self, rect):
    if ui_state.started:
      gui_app.pop_widget()
      return
    if time.monotonic() - self.last_poll >= 5:
      self.last_poll = time.monotonic()
      try:
        self.data = read_summary(ROOT)
        self.message = 'Last saved: ' + date_label(self.data['updated'])
      except FileNotFoundError:
        self.data = None
        self.message = 'No saved history. Optional recorder may not be installed or running.'
      except Exception:
        self.data = None
        self.message = 'History unavailable or invalid. Driving is unaffected.'
    rl.draw_rectangle_rec(rect, rl.Color(15, 20, 28, 255))
    x, y = rect.x + 35, rect.y + 30
    self.text('Thermal exposure history', x, y, 54)
    self.text('Internal sensors, NOT OLED temperature. No screen-safety guarantee.', x, y+75, 28, rl.YELLOW)
    self.text(self.message, x, y+115, 27)
    self.buttons = []
    for i, (action, label) in enumerate([('Mode', self.mode.upper()), ('Sensor', SENSORS[self.sensor]), ('Close', 'Close')]):
      button = rl.Rectangle(x+i*360, y+165, 335, 75)
      self.buttons.append((button, action))
      rl.draw_rectangle_rec(button, rl.Color(40, 55, 75, 255))
      self.text(label, button.x+20, button.y+20, 30)
    if self.data is None:
      return
    self.text('Since first dated sample: '+date_label(self.data['since']), x, y+270, 27)
    columns = [0, 340, 615, 935, 1280]
    for dx, label in zip(columns, ['Sensor', 'Peak C', 'Worst 10 min C', 'Worst 1 hour C', 'Recorded hours'], strict=True):
      self.text(label, x+dx, y+330, 30, rl.SKYBLUE)
    for row, sensor in enumerate(SENSORS):
      record = self.data['records'][f'{self.mode}/{sensor}']
      values = [sensor, *[f"{record[k]['value']:.1f}" if record[k] else '--' for k in ('peak', 'avg600', 'avg3600')],
                f"{record['seconds']/3600:.1f}"]
      for dx, label in zip(columns, values, strict=True):
        self.text(label, x+dx, y+385+row*48, 32)
    record = self.data['records'][f'{self.mode}/{SENSORS[self.sensor]}']
    stamp = record['peak'].get('time') if record['peak'] else None
    self.text(f'{SENSORS[self.sensor]} peak date: {date_label(stamp)}', x, y+655, 28)
    self.text('Exposure bins (not damage limits): total hours / longest continuous minutes', x, y+705, 28, rl.SKYBLUE)
    for i, threshold in enumerate(THRESHOLDS):
      key = str(threshold)
      self.text(f">{threshold}C: {record['above'][key]/3600:.1f}h / {record['longest'][key]/60:.1f}m",
                x+(i % 3)*590, y+755+(i//3)*45, 28)
    self.text('Lifetime means since recording began. Powered-off and missing-data periods are unknown.', x, y+900, 26, rl.YELLOW)
    self.text('Full windows only; -- means insufficient continuous data. Summary <=64 KiB; saves every minute.', x, y+945, 25)
