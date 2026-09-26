import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pyray as rl

from openpilot.selfdrive.ui.onroad import co2_overlay
from openpilot.selfdrive.ui.onroad import augmented_road_view
from openpilot.selfdrive.ui.layouts import home
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
  assert co2_overlay.latest_co2(tmp_path, 1000.) == (1159, 1900.)  # interval 120 s: shown for max(900, 3*interval)
  assert co2_overlay.latest_co2(tmp_path, 1901.) is None
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
    alive = {'carState': True}
  fake_ui = SimpleNamespace(started=True, started_frame=1, engaged=False, sm=FakeSM(carState=state))
  monkeypatch.setattr(aranet_screen, 'ui_state', fake_ui)
  assert aranet_screen.graph_allowed()
  FakeSM.alive = {'carState': False}  # last message said parked, but carState stopped arriving
  assert not aranet_screen.graph_allowed()
  FakeSM.alive = {'carState': True}
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
  monkeypatch.setattr(co2_overlay.rl, 'draw_text_ex', lambda font, text, pos, size, *a: draws.append(('text', text, pos, size, a[-1])))
  monkeypatch.setattr(co2_overlay, 'time', SimpleNamespace(time=lambda: 1000.))
  from openpilot.selfdrive.ui import ui_state as ui_state_module
  monkeypatch.setattr(ui_state_module, 'ui_state', SimpleNamespace(started=False, sm={'driverMonitoringState': SimpleNamespace(isRHD=False)}))
  overlay = SimpleNamespace(_latest=(1159, 1900.), _font=object(), _badge=None)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  text_draws = [d for d in draws if d[0] == 'text']
  assert [d[1] for d in text_draws] == ['CO2 ', '1159 ppm']  # plain "2", not the unsupported ₂ glyph
  assert text_draws[0][4] == co2_overlay.HIGH_CO2_COLOR  # over 1000ppm: "CO2" label is red
  assert text_draws[1][4] == co2_overlay.COLORS.WHITE  # the ppm figure stays white
  assert text_draws[-1][3] == co2_overlay.FONT_SIZES.max_speed
  assert 1750 < text_draws[0][2].x < 2130
  assert 900 < text_draws[0][2].y < 1050
  draws.clear()
  overlay._latest = (1159, 999.)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert overlay._badge is None
  assert draws == []


def test_badge_label_color_only_turns_red_above_threshold(monkeypatch):
  draws = []
  monkeypatch.setattr(co2_overlay, 'measure_text_cached', lambda font, text, size: rl.Vector2(260, 40))
  monkeypatch.setattr(co2_overlay.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(co2_overlay.rl, 'draw_text_ex', lambda font, text, pos, size, *a: draws.append((text, a[-1])))
  monkeypatch.setattr(co2_overlay, 'time', SimpleNamespace(time=lambda: 1000.))
  from openpilot.selfdrive.ui import ui_state as ui_state_module
  monkeypatch.setattr(ui_state_module, 'ui_state', SimpleNamespace(started=False, sm={'driverMonitoringState': SimpleNamespace(isRHD=False)}))
  overlay = SimpleNamespace(_latest=(1000, 1900.), _font=object(), _badge=None)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert draws[0] == ('CO2 ', co2_overlay.COLORS.WHITE)  # exactly at threshold: not yet red
  draws.clear()
  overlay._latest = (1001, 1900.)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  assert draws[0] == ('CO2 ', co2_overlay.HIGH_CO2_COLOR)
  assert draws[1] == ('1001 ppm', co2_overlay.COLORS.WHITE)


def test_badge_moves_above_right_hand_drive_monitor_and_can_be_hidden(monkeypatch):
  draws = []
  monkeypatch.setattr(co2_overlay, 'measure_text_cached', lambda font, text, size: rl.Vector2(260, 40))
  monkeypatch.setattr(co2_overlay.rl, 'draw_rectangle_rounded', lambda *a: None)
  monkeypatch.setattr(co2_overlay.rl, 'draw_text_ex', lambda font, text, pos, size, *a: draws.append(pos.y))
  monkeypatch.setattr(co2_overlay, 'time', SimpleNamespace(time=lambda: 1000.))
  from openpilot.selfdrive.ui import ui_state as ui_state_module
  state = SimpleNamespace(isRHD=False)
  fake_ui = SimpleNamespace(started=False, sm={'driverMonitoringState': state})
  monkeypatch.setattr(ui_state_module, 'ui_state', fake_ui)
  overlay = SimpleNamespace(_latest=(1159, 1900.), _font=object(), _badge=None)
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  state.isRHD = True
  fake_ui.started = True
  co2_overlay.Co2Overlay._render(overlay, rl.Rectangle(30, 30, 2100, 1020))
  # Two text draws (label + ppm) per render, sharing one baseline y each time.
  assert draws[0] == draws[1]
  assert draws[2] == draws[3]
  assert draws[0] - draws[2] == co2_overlay.UI_CONFIG.button_size + 20
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


def test_parked_home_badge_opens_same_settings_graph(monkeypatch):
  opened = []
  monkeypatch.setattr(home.gui_app, 'push_widget', opened.append)
  monkeypatch.setattr(aranet_screen, 'AranetLayout', lambda: 'existing Aranet graph')
  view = SimpleNamespace(current_state=home.HomeLayoutState.HOME,
                         _co2_overlay=SimpleNamespace(hit_test=lambda pos: True))
  home.HomeLayout._handle_mouse_release(view, rl.Vector2(1900, 980))
  assert opened == ['existing Aranet graph']
