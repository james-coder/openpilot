"""Native interaction checks. Run with DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1."""
import os
from types import SimpleNamespace

import pytest
from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='Native UI checks require a display')


@pytest.fixture(scope='module')
def window():
  import pyray as rl
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.system.ui.lib.application import gui_app
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('CAN interaction tests')
    yield gui_app
    rl.close_window()


@pytest.fixture
def layout(window, monkeypatch):
  import pyray as rl
  import openpilot.selfdrive.ui.layouts.settings.can_diagnostics as module
  from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession
  class State(dict):
    valid = {'carState': True}
    alive = {'carState': True}
  state = State(carState=SimpleNamespace(vEgo=0.))
  monkeypatch.setattr(module, 'ui_state', SimpleNamespace(started=False, engaged=False, sm=state,
                                                        CP=SimpleNamespace(carFingerprint=CAR.CHEVROLET_VOLT)))
  monkeypatch.setattr(module.device, 'set_override_interactive_timeout', lambda _: None)
  monkeypatch.setattr(module.messaging, 'recv_one_or_none', lambda _: None)
  monkeypatch.setattr(module.messaging, 'sub_sock', lambda *a, **kw: object())
  widget = module.CanDiagnosticsLayout()
  widget.session = InspectionSession(CAR.CHEVROLET_VOLT)
  widget._sock = object()
  widget._active = True
  widget.session.ingest([(1, [CANPacker('gm_global_a_powertrain_generated').make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1200})])])
  widget._refresh()
  def render(events=()):
    window._mouse_events = list(events)
    rl.begin_drawing()
    widget.render(rl.Rectangle(0, 0, 2160, 1080))
    rl.end_drawing()
    window._mouse_events = []
    assert not widget._faulted
  widget.test_render = render
  render()
  yield widget
  widget.hide_event()


def test_favorite_click_saves_and_restores_with_real_params(layout):
  from openpilot.common.params import Params
  from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession
  from openpilot.system.ui.lib.application import MouseEvent, MousePos
  key = (0, 201, 'EngineRPM')
  row = next(row for row in layout._scroller._items if row.key == key)
  point = MousePos(row.favorite_rect.x+60, row.favorite_rect.y+60)
  layout.test_render([MouseEvent(point, 0, True, False, True, 1.)])
  layout.test_render([MouseEvent(point, 0, False, True, False, 1.1)])
  layout.test_render()
  assert layout.screen == 'home'
  document = Params().get('CanDiagnosticsPreferences')
  assert InspectionSession(CAR.CHEVROLET_VOLT, document).favorites == [key]


def test_scroll_does_not_activate_row_or_favorite(layout):
  from openpilot.system.ui.lib.application import MouseEvent, MousePos
  row = layout._scroller._items[0]
  x, y = row.rect.x+row.rect.width-80, row.rect.y+50
  def event(y, pressed=False, released=False, down=False, t=1.):
    return MouseEvent(MousePos(x, y), 0, pressed, released, down, t)
  layout.test_render([event(y, pressed=True, down=True)])
  layout.test_render([event(y-150, down=True, t=1.1)])
  layout.test_render([event(y-150, released=True, t=1.2)])
  layout.test_render()
  assert layout.screen == 'home' and not layout.session.favorites


def test_signal_picker_search_preserves_slot_and_back_position(layout):
  layout.session.toggle_favorite((0, 201, 'EngineRPM'))
  layout.session.select_graph((0, 201, 'EngineRPM'))
  layout._set_tab('compare')
  layout._pick(1)
  layout._open_search()
  assert layout.screen == 'search'
  layout._search_finished('EngineTPS')
  assert layout.screen == 'pick' and layout._pick_slot == 1
  assert any(row.key == (0, 201, 'EngineTPS') for row in layout._scroller._items)
  layout.open_row((0, 201, 'EngineTPS'), 'signal')
  assert layout.screen == 'compare'
  assert len(layout.session.histories) == 2
  layout.back()
  assert layout.screen == 'home'


def test_message_payload_growth_refreshes_raw_rows(layout):
  layout.session.ingest([(2, [(0x10000, b'\x01'*8, 0)])])
  layout._refresh()
  layout.open_row((0, 0x10000, None), 'signal')
  layout._info.scroll_panel.set_offset(-100)
  layout.session.ingest([(3, [(0x10000, b'\x02'*64, 0)])])
  layout._refresh()
  raw = [row for row in layout._info._items if row.title.startswith('Raw bytes')]
  assert len(raw) == 8 and raw[-1].value() == '02 02 02 02 02 02 02 02'
  assert layout._info.scroll_panel.offset == -100


def test_fault_still_allows_exit(layout):
  layout._faulted = True
  layout.on_back = layout.hide_event
  layout.enqueue(layout.back)
  layout._update_state()
  assert not layout._active and layout.session is None and layout._sock is None


def test_odometer_header_precision_age_and_freeze(layout, monkeypatch):
  import openpilot.selfdrive.ui.layouts.settings.can_diagnostics as module
  from openpilot.selfdrive.ui.layouts.settings.can_diagnostics_data import ODOMETER_KEY
  layout.session.ingest([(10, [(0x120, bytes.fromhex('00b0d4db00'), 0)])])
  layout._refresh()
  updated = layout.display.rows[ODOMETER_KEY].last_updated
  texts = []
  original = module.label
  def capture(text, *args, **kwargs):
    texts.append(text)
    original(text, *args, **kwargs)
  monkeypatch.setattr(module, 'label', capture)
  monkeypatch.setattr(layout, '_update_state', lambda: None)
  layout.display = layout.session.capture(now=updated+6)
  layout.test_render()
  assert '112,515 mi' in texts and 'Odometer | 6s ago' in texts
  assert not any('STALE' in t for t in texts if t.startswith('Odometer'))
  texts.clear()
  layout.display = layout.session.capture(now=updated+16)
  layout.test_render()
  assert 'Odometer | STALE | 16s ago' in texts
  layout._freeze()
  layout.session.ingest([(20, [(0x120, bytes.fromhex('00b0dfff00'), 0)])])
  layout._refresh()
  texts.clear()
  layout.test_render()
  assert '112,515 mi' in texts


def test_back_preserves_browse_scroller_and_filter(layout):
  original = layout._scroller
  layout.open_row((0, 201, 'EngineRPM'), 'signal')
  layout._decoding()
  layout.back()
  assert layout.screen == 'signal'
  layout.back()
  assert layout._scroller is original
  assert layout.screen == 'home' and layout.filter == 'live'


def test_exit_action_does_not_reopen_can_socket(layout):
  layout.on_back = layout.hide_event
  layout.enqueue(layout.back)
  layout._update_state()
  assert layout._sock is None and layout.session is None and not layout._active


def test_fullscreen_settings_does_not_dispatch_hidden_sidebar_touches(layout):
  from openpilot.selfdrive.ui.layouts.settings.settings import SettingsLayout, PanelType
  from openpilot.system.ui.widgets import Widget
  from openpilot.system.ui.lib.application import MousePos
  settings = SettingsLayout.__new__(SettingsLayout)
  Widget.__init__(settings)
  settings._current_panel = PanelType.CAN_DIAGNOSTICS
  # Intentionally omit sidebar hitboxes: the handler must not touch them.
  settings._handle_mouse_release(MousePos(100, 100))


@pytest.mark.parametrize('screen', ['home', 'compare', 'signal', 'message', 'decoding', 'bits', 'bit_info',
                                   'catalog', 'coverage', 'search', 'pick', 'more', 'buses', 'filters'])
def test_onroad_transition_releases_every_inspector_subview(layout, screen, monkeypatch):
  import openpilot.selfdrive.ui.layouts.main as module
  from openpilot.selfdrive.ui.layouts.settings.settings import SettingsLayout, PanelType
  from openpilot.system.ui.widgets import Widget
  main = module.MainLayout.__new__(module.MainLayout)
  settings = SettingsLayout.__new__(SettingsLayout)
  Widget.__init__(settings)
  settings._current_panel = PanelType.CAN_DIAGNOSTICS
  settings._panels = {PanelType.CAN_DIAGNOSTICS: SimpleNamespace(instance=layout)}
  main._prev_onroad = False
  main._current_mode = module.MainState.SETTINGS
  main._sidebar = SimpleNamespace(set_visible=lambda _: None)
  main._layouts = {module.MainState.SETTINGS: settings, module.MainState.ONROAD: SimpleNamespace(show_event=lambda: None)}
  layout.screen = screen
  layout.session.toggle_freeze()
  monkeypatch.setattr(module, 'ui_state', SimpleNamespace(started=True, is_body=False))
  main._handle_onroad_transition()
  assert main._current_mode == module.MainState.ONROAD
  assert layout.session is None and layout._sock is None and layout._keyboard is None


def test_new_widgets_have_large_touch_targets(layout):
  for identity in layout._used_buttons:
    rect = layout._buttons[identity].rect
    assert rect.width >= 120 and rect.height >= 120
  assert sum(row.rect.y+row.rect.height <= 1056 for row in layout._scroller._items) >= 4
