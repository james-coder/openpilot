"""Native checks of the Dashcam widget against synthetic footage. Run with DISPLAY=:0 BIG=1 SCALE=1 OFFSCREEN=1.

These run the real widget code (layout, touch handling, texture upload, close paths) on this workstation's display with stand-ins for
ui_state and the device. They are NOT device validation: the comma 3X's GPU, decode speed, screen timeout and engagement behaviour
are untested by them."""
import os
import time
from types import SimpleNamespace

import pytest

from openpilot.system.review.player.events import events_path_for
from openpilot.system.review.player.session import DashcamSession
from openpilot.system.review.tests.player_helpers import ROUTE, event_records, make_segment, wait_until, write_events

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='Native UI checks require a display')

MONO0 = 1000.0


@pytest.fixture(scope='module')
def window():
  import pyray as rl
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.system.ui.lib.application import gui_app
  with OpenpilotPrefix():
    rl.set_config_flags(rl.ConfigFlags.FLAG_WINDOW_HIDDEN)
    gui_app.init_window('Dashcam native tests')
    yield gui_app
    rl.close_window()


class Rig:
  def __init__(self, tmp_path, window, monkeypatch):
    import pyray as rl
    import openpilot.selfdrive.ui.layouts.settings.dashcam as module
    from cereal import log
    self.rl, self.module, self.window = rl, module, window
    self.root = tmp_path / 'media' / '0' / 'realdata'
    self.root.mkdir(parents=True)
    make_segment(self.root, 4, frames=60, mono0=MONO0)
    write_events(events_path_for(self.root), event_records(f'{ROUTE}--4', mono=MONO0 + 1.5))
    self.flags, self.timeouts, self.popped = set(), [], []
    self.state = SimpleNamespace(started=False, is_metric=False,
                                 sm={'deviceState': SimpleNamespace(thermalStatus=log.DeviceState.ThermalStatus.ok)})
    monkeypatch.setattr(module, 'ui_state', self.state)
    monkeypatch.setattr(module, 'device', SimpleNamespace(awake=True, set_override_interactive_timeout=self.timeouts.append))
    monkeypatch.setattr(window, 'pop_widget', lambda *a: self.popped.append(a))
    monkeypatch.setattr(window, 'widget_in_stack', lambda w: True)
    monkeypatch.setattr(window, 'get_active_widget', lambda: self.widget)
    session = DashcamSession(self.root, protect_fn=lambda p: self.flags.add(p), getxattr_fn=lambda p, a: b'1' if p in self.flags else None,
                             drop_realtime_fn=lambda: None, logger=lambda m: None)
    self.widget = module.DashcamLayout(session=session)
    self.widget.show_event()
    self.session = session
    self.t = 1.0

  def render(self, events=(), frames=1):
    rl = self.rl
    for _ in range(frames):
      self.window._mouse_events = list(events)
      events = ()
      rl.begin_drawing()
      self.widget.render(rl.Rectangle(0, 0, self.window.width, self.window.height))
      rl.end_drawing()
    self.window._mouse_events = []

  def until(self, cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      self.render()
      if cond():
        return True
      time.sleep(0.01)
    return cond()

  def tap(self, rect_or_point):
    from openpilot.system.ui.lib.application import MouseEvent, MousePos
    x, y = rect_or_point if len(rect_or_point) == 2 else (rect_or_point[0] + rect_or_point[2] / 2, rect_or_point[1] + rect_or_point[3] / 2)
    self.t += 0.1
    self.render([MouseEvent(MousePos(x, y), 0, True, False, True, self.t)])
    self.t += 0.1
    self.render([MouseEvent(MousePos(x, y), 0, False, True, False, self.t)])

  def drag(self, x0, y0, x1, y1):
    from openpilot.system.ui.lib.application import MouseEvent, MousePos
    steps = 6
    self.t += 0.1
    self.render([MouseEvent(MousePos(x0, y0), 0, True, False, True, self.t)])
    for i in range(1, steps + 1):
      self.t += 0.02
      self.render([MouseEvent(MousePos(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps), 0, False, False, True, self.t)])
    self.t += 0.02
    self.render([MouseEvent(MousePos(x1, y1), 0, False, True, False, self.t)])

  def open_event(self):
    assert self.until(lambda: self.widget._view is not None and self.widget._view.rows)
    self.tap((self.widget._geom.list[0] + 100, self.widget._geom.list[1] + 40))
    assert self.until(lambda: self.widget._view.clip_state == 'ready' and not self.widget._view.buffering and self.widget._texture is not None)


@pytest.fixture
def rig(tmp_path, window, monkeypatch):
  r = Rig(tmp_path, window, monkeypatch)
  yield r
  r.widget.hide_event()
  r.session.join(5)


def test_event_list_selection_shows_a_video_frame_on_a_gpu_texture(rig):
  rig.open_event()
  v = rig.widget._view
  assert v.event_time == pytest.approx(1.5) and v.position == 0.0
  assert rig.widget._texture is not None and rig.widget._texture_size == (526, 330)
  assert rig.timeouts[0] == rig.module.SCREEN_TIMEOUT_S
  assert not rig.widget._closed and not rig.popped


def test_play_pause_steps_jumps_rates_and_scrub_by_touch(rig):
  rig.open_event()
  L, s = rig.widget._geom, rig.session
  rig.tap(L.buttons['play'])
  assert s.view().playing
  rig.tap(L.buttons['play'])
  assert not s.view().playing
  f0 = s._clock.frame
  rig.tap(L.buttons['stepfwd'])
  assert s._clock.frame == f0 + 1 and s.view().position == pytest.approx((f0 + 1) / 20) and not s.view().playing
  rig.tap(L.buttons['stepback'])
  rig.tap(L.buttons['stepback'])
  assert s._clock.frame == max(f0 - 1, 0)
  rig.tap(L.buttons['fwd5'])
  assert s.view().position == pytest.approx(59 / 20)        # 60 frames at 20 fps: +5 s clamps to the last frame
  rig.tap(L.buttons['back5'])
  assert s.view().position == 0.0
  rig.tap(L.rates[0.25])
  assert s.view().rate == 0.25
  x, y, w, h = L.scrub
  rig.drag(x + 5, y + h / 2, x + w * 0.5, y + h / 2 + 40)
  assert s.view().position == pytest.approx(0.5 * s.view().duration, abs=0.1)
  rig.tap(L.video)
  assert s.view().playing
  assert rig.until(lambda: s.view().position > 0.5 * s.view().duration + 0.05)
  assert rig.until(lambda: rig.widget._texture_frame is not None and rig.widget._texture_frame[1] > 30)


def test_protect_button_sets_the_flag_and_the_list_shows_it(rig):
  rig.open_event()
  rig.tap(rig.widget._geom.protect)
  assert wait_until(lambda: str(rig.root / f'{ROUTE}--4') in rig.flags)
  assert rig.until(lambda: rig.widget._view.segment_status.protected is True)


def test_starting_the_car_closes_the_screen_at_once_and_releases_everything(rig):
  rig.open_event()
  rig.state.started = True
  rig.render()
  assert rig.popped and rig.widget._closed
  assert rig.timeouts[-1] is None                      # screen timeout restored
  assert rig.widget._texture is None                   # GPU texture freed
  assert rig.session.join(5)                           # decode worker has exited
  rig.render(frames=3)                                 # later frames are harmless
  assert rig.popped == [(None,)]


def test_an_exception_while_drawing_closes_the_screen_instead_of_reaching_the_main_loop(rig, monkeypatch):
  rig.open_event()

  def boom():
    raise RuntimeError('draw failure')
  monkeypatch.setattr(rig.widget, '_draw', boom)
  rig.render()
  assert rig.popped and rig.widget._closed and rig.timeouts[-1] is None
  rig.render(frames=2)


def test_closing_pops_only_its_own_entry_when_another_dialog_is_on_top(rig, monkeypatch):
  rig.open_event()
  monkeypatch.setattr(rig.window, '_nav_stack', ['main', rig.widget, 'dialog'])
  monkeypatch.setattr(rig.window, 'get_active_widget', lambda: 'dialog')
  rig.widget._close()
  assert rig.popped == [(1,)] and rig.widget._closed and rig.timeouts[-1] is None


def test_an_exception_anywhere_in_the_base_render_path_closes_the_screen(rig, monkeypatch):
  rig.open_event()

  def boom(*a, **k):
    raise RuntimeError('touch routing failure')
  monkeypatch.setattr(rig.widget, '_process_mouse_events', boom)
  rig.render()
  assert rig.popped and rig.widget._closed and rig.session.join(5)


def test_an_exception_while_updating_closes_the_screen(rig, monkeypatch):
  rig.open_event()
  monkeypatch.setattr(rig.session, 'update', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('update failure')))
  rig.render()
  assert rig.popped and rig.widget._closed and rig.session.join(5)


def test_hide_event_restores_the_timeout_and_frees_the_texture_and_is_repeatable(rig):
  rig.open_event()
  rig.widget.hide_event()
  rig.widget.hide_event()
  assert rig.timeouts[-1] is None and rig.widget._texture is None and rig.session.join(5)


def test_a_hot_device_pauses_playback(rig):
  from cereal import log
  rig.open_event()
  rig.tap(rig.widget._geom.buttons['play'])
  assert rig.session.view().playing
  rig.state.sm['deviceState'] = SimpleNamespace(thermalStatus=log.DeviceState.ThermalStatus.overheated)
  rig.render()
  assert not rig.session.view().playing and rig.widget._view.hot


def test_an_unreadable_deviceState_does_not_count_as_hot(rig):
  rig.state.sm = {}
  rig.render()
  assert not rig.widget._closed and rig.widget._view is not None and not rig.widget._view.hot


def test_empty_and_error_states_render(tmp_path, window, monkeypatch):
  r = Rig(tmp_path, window, monkeypatch)
  try:
    events_path_for(r.root).write_bytes(b'garbage\n')
    r.session.refresh()
    assert r.until(lambda: r.widget._view is not None and r.widget._view.events_state == 'ok' and not r.widget._view.rows)
    r.render(frames=3)
    assert not r.widget._closed
  finally:
    r.widget.hide_event()
    r.session.join(5)
