"""Local cabin-air history; independent scales, gaps retained, no car controls."""
import json
import sqlite3
import time
import pyray as rl

from openpilot.selfdrive.car.aranet import ROOT, read_history, plot_samples
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget


def status_lines(message, width, measure):
  lines, line = [], ''
  for word in str(message).split():
    candidate = (line + ' ' + word).strip()
    if measure(candidate) <= width:
      line = candidate
    else:
      if line:
        lines.append(line)
      line = word
  if line:
    lines.append(line)
  result = []
  for i, line in enumerate(lines[:2]):
    truncated = measure(line) > width or (i == 1 and len(lines) > 2)
    if truncated:
      while line and measure(line + '...') > width:
        line = line[:-1]
      line += '...'
    result.append(line)
  return result


class AranetLayout(Widget):
  def __init__(self):
    super().__init__()
    self.font = gui_app.font(FontWeight.MEDIUM)
    self.hours = 24
    self.rows = []
    self.poll = 0
    self.message = ''
    self.buttons = []

  def _handle_mouse_release(self, pos):
    for rect, action in self.buttons:
      if rl.check_collision_point_rec(pos, rect):
        if action == 'Close':
          gui_app.pop_widget()
        elif action == 'Pause / Resume':
          try:
            ROOT.mkdir(exist_ok=True)
            path = ROOT / 'paused'
            if path.exists():
              path.unlink()
            else:
              path.touch()
          except OSError:
            self.message = 'Unable to change logging state'
        else:
          self.hours = int(action)
        self.poll = 0

  def text(self, value, x, y, size=28, color=rl.WHITE):
    rl.draw_text_ex(self.font, value, rl.Vector2(x, y), size, 0, color)

  def _render(self, r):
    if ui_state.started:
      gui_app.pop_widget()
      return
    now = time.time()  # noqa: TID251 -- persisted history uses wall time across reboots
    if time.monotonic() - self.poll > 5:
      self.poll = time.monotonic()
      try:
        self.rows = read_history(seconds=self.hours * 3600)
        state = json.loads((ROOT / 'status.json').read_text())
        if not isinstance(state['message'], str):
          raise ValueError('Invalid status message')
        self.message = state['message'][:240] if 0 <= now - state['time'] < 90 else 'Collector offline; history retained'
      except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        self.message = 'Collector unavailable; history retained'
    rl.draw_rectangle_rec(r, rl.Color(8, 14, 24, 255))
    self.buttons = []
    for i, label in enumerate(['2', '24', '168', '720', 'Pause / Resume', 'Close']):
      button = rl.Rectangle(r.x+25+i*(r.width-50)/6, r.y+20, (r.width-65)/6, 65)
      self.buttons.append((button, label))
      rl.draw_rectangle_rounded(button, .15, 6, rl.Color(45, 62, 80, 255))
      self.text({'2': '2 hours', '24': '24 hours', '168': '7 days', '720': '30 days'}.get(label, label), button.x+12, button.y+18, 27)
    latest = self.rows[-1] if self.rows else None
    age = max(0, now-latest[0]) if latest else None
    title = f'CO2  {latest[1]:.0f} ppm' if latest else 'CO2  -- ppm'
    self.text(title, r.x+35, r.y+108, 60, rl.Color(0, 235, 255, 255))
    freshness = f'Sample age {age/60:.1f} min' if latest else 'No readings in this window'
    if latest and age > max(180, latest[4]*2):
      freshness = 'STALE / sensor missing | ' + freshness
    self.text(f'Aranet4 2954E | {freshness}', r.x+35, r.y+180, 29)
    def measure(text):
      return measure_text_cached(self.font, text, 24).x
    for i, line in enumerate(status_lines(self.message, r.width-70, measure)):
      self.text(line, r.x+35, r.y+223+i*27, 24)
    available = max(270, r.height-360)
    for index, (column, label, color, fraction) in enumerate([
      (1, 'CO2 (ppm)', rl.Color(0, 235, 255, 255), .50),
      (2, 'Temperature (C)', rl.Color(255, 180, 65, 255), .25),
      (3, 'Humidity (%)', rl.Color(235, 115, 255, 255), .25),
    ]):
      top = r.y+280+available*[0, .5, .75][index]
      height = available*fraction-48
      x, width = r.x+160, r.width-200
      self.text(label + (f'  {latest[column]:.1f}' if latest else ''), r.x+35, top-4, 24, color)
      if not self.rows:
        continue
      vals = [row[column] for row in self.rows]
      lo, hi = min(vals), max(vals)
      pad = max((hi-lo)*.1, [50, 1, 2][index])
      lo, hi = lo-pad, hi+pad
      for j in range(3):
        y = top+25+height*j/2
        rl.draw_line_ex(rl.Vector2(x, y), rl.Vector2(x+width, y), 1, rl.Color(65, 77, 95, 255))
        self.text(f'{hi-(hi-lo)*j/2:.0f}', r.x+65, y-10, 22)
      previous = None
      for row in plot_samples(self.rows, column):
        if row is None:
          previous = None
          continue
        point = rl.Vector2(x+width*(1-(now-row[0])/(self.hours*3600)), top+25+height*(hi-row[column])/(hi-lo))
        if previous and row[0]-previous[0][0] <= max(180, 2*max(row[4], previous[0][4])):
          rl.draw_line_ex(previous[1], point, 3, color)
        rl.draw_circle_v(point, 2, color)
        previous = row, point
    self.text(f'{self.hours} hours ago', r.x+160, r.y+r.height-65, 24)
    self.text('Now', r.x+r.width-100, r.y+r.height-65, 24)
    self.text('Local only | Up to 30 days / 21,600 samples | 4 MiB database | No automatic vent control', r.x+35, r.y+r.height-30, 23)
