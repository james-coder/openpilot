import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pyray as rl

from openpilot.selfdrive.ui.onroad import co2_overlay
from openpilot.selfdrive.ui.onroad import augmented_road_view
from openpilot.selfdrive.ui.layouts.settings import aranet as aranet_screen


def sample(root: Path, now=1000., ppm=1159, interval=120, state='recording', status_age=0, sample_age=0):
  root.mkdir(exist_ok=True)
  (root / 'status.json').write_text(json.dumps({'time': now-status_age, 'state': state}))
  with sqlite3.connect(root / 'history.sqlite') as db:
    db.execute('CREATE TABLE readings (t REAL, co2 REAL, interval INTEGER)')
    db.execute('INSERT INTO readings VALUES (?,?,?)', (now-sample_age, ppm, interval))


def test_latest_co2_requires_live_collector_and_fresh_valid_sample(tmp_path):
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None  # cold boot / feature absent
  sample(tmp_path)
  assert co2_overlay.latest_co2(tmp_path, 1000.) == (1159, 1240.)
  assert co2_overlay.latest_co2(tmp_path, 1241.) is None
  (tmp_path / 'status.json').write_text(json.dumps({'time': 900, 'state': 'recording'}))
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None  # crashed/stopped
  (tmp_path / 'status.json').write_text(json.dumps({'time': 1000, 'state': 'paused'}))
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None


def test_latest_co2_handles_corrupt_and_unreadable_data(tmp_path, monkeypatch):
  sample(tmp_path)
  (tmp_path / 'status.json').write_text('{invalid')
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None
  (tmp_path / 'status.json').write_text(json.dumps({'time': 1000, 'state': 'recording'}))
  original_open = Path.open

  def denied(path, *args, **kwargs):
    if path.name == 'status.json':
      raise PermissionError('denied')
    return original_open(path, *args, **kwargs)

  monkeypatch.setattr(Path, 'open', denied)
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None
  monkeypatch.undo()
  with sqlite3.connect(tmp_path / 'history.sqlite') as db:
    db.execute('UPDATE readings SET co2=99999')
  assert co2_overlay.latest_co2(tmp_path, 1000.) is None


def test_full_screen_graph_only_while_parked_and_disengaged(monkeypatch):
  state = SimpleNamespace(canValid=True, gearShifter='park', vEgo=0.)
  class FakeSM(dict):
    recv_frame = {'carState': 2}
  fake_ui = SimpleNamespace(started=True, started_frame=1, engaged=False, sm=FakeSM(carState=state))
  monkeypatch.setattr(aranet_screen, 'ui_state', fake_ui)
  assert aranet_screen.graph_allowed()
  fake_ui.engaged = True
  assert not aranet_screen.graph_allowed()
  fake_ui.engaged = False
  state.gearShifter = 'drive'
  assert not aranet_screen.graph_allowed()
  state.gearShifter = 'park'
  state.vEgo = 1.
  assert not aranet_screen.graph_allowed()
  fake_ui.started = False
  assert aranet_screen.graph_allowed()


def test_badge_uses_max_font_size_and_lower_right_and_hides_when_stale(monkeypatch):
  draws = []
  monkeypatch.setattr(co2_overlay, 'measure_text_cached', lambda font, text, size: rl.Vector2(260, 40))
  monkeypatch.setattr(co2_overlay.rl, 'draw_rectangle_rounded', lambda rect, *a: draws.append(('box', rect)))
  monkeypatch.setattr(co2_overlay.rl, 'draw_text_ex', lambda font, text, pos, size, *a: draws.append(('text', text, pos, size)))
  monkeypatch.setattr(co2_overlay, 'time', SimpleNamespace(time=lambda: 1000.))
  from openpilot.selfdrive.ui import ui_state as ui_state_module
  monkeypatch.setattr(ui_state_module, 'ui_state', SimpleNamespace(sm={'driverMonitoringState': SimpleNamespace(isRHD=False)}))
  overlay = SimpleNamespace(_latest=(1159, 1240.), _font=object(), _badge=None)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert draws[-1][1] == 'CO₂ 1159 ppm'
  assert draws[-1][3] == co2_overlay.FONT_SIZES.max_speed
  assert 1750 < draws[-1][2].x < 2130
  assert 900 < draws[-1][2].y < 1050
  overlay._latest = (1159, 999.)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert overlay._badge is None
  assert len(draws) == 2


def test_badge_moves_above_right_hand_drive_monitor_and_can_be_hidden(monkeypatch):
  draws = []
  monkeypatch.setattr(co2_overlay, 'measure_text_cached', lambda font, text, size: rl.Vector2(260, 40))
  monkeypatch.setattr(co2_overlay.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(co2_overlay.rl, 'draw_text_ex', lambda font, text, pos, size, *a: draws.append(pos.y))
  monkeypatch.setattr(co2_overlay, 'time', SimpleNamespace(time=lambda: 1000.))
  from openpilot.selfdrive.ui import ui_state as ui_state_module
  state = SimpleNamespace(isRHD=False)
  monkeypatch.setattr(ui_state_module, 'ui_state', SimpleNamespace(sm={'driverMonitoringState': state}))
  overlay = SimpleNamespace(_latest=(1159, 1240.), _font=object(), _badge=None)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  state.isRHD = True
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert draws[0] - draws[1] == co2_overlay.UI_CONFIG.button_size + 20
  co2_overlay.Co2Overlay.hide(overlay)
  assert overlay._badge is None


def test_badge_tap_uses_existing_graph_without_opening_while_engaged(monkeypatch):
  opened = []
  monkeypatch.setattr(augmented_road_view.gui_app, 'push_widget', opened.append)
  monkeypatch.setattr(aranet_screen, 'AranetLayout', lambda **kwargs: kwargs)
  monkeypatch.setattr(aranet_screen, 'graph_allowed', lambda: True)
  view = SimpleNamespace(_co2_overlay=SimpleNamespace(hit_test=lambda pos: True),
                         _hud_renderer=SimpleNamespace(user_interacting=lambda: False),
                         _click_callback=lambda: opened.append('sidebar'))
  augmented_road_view.AugmentedRoadView._handle_mouse_press(view, rl.Vector2(1900, 980))
  assert opened == [{'allow_parked_onroad': True}]
  opened.clear()
  monkeypatch.setattr(aranet_screen, 'graph_allowed', lambda: False)
  augmented_road_view.AugmentedRoadView._handle_mouse_press(view, rl.Vector2(1900, 980))
  assert opened == []
