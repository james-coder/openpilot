"""Wiring checks for the optional Dashcam screen that need no display: the Device-panel entry cannot break the panel, the player logic
imports no UI code, and the widget keeps its fail-closed structure. Source is parsed and the real entry function is executed with
stand-ins, because importing the UI modules themselves needs a display. These do not exercise the widget; see test_dashcam_native.py."""
import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from openpilot.common.basedir import BASEDIR

ROOT = Path(BASEDIR)
DEVICE = ROOT / 'selfdrive/ui/layouts/settings/device.py'
WIDGET = ROOT / 'selfdrive/ui/layouts/settings/dashcam.py'
PLAYER = ROOT / 'system/review/player'
MODULE = 'openpilot.selfdrive.ui.layouts.settings.dashcam'


def device_class():
  tree = ast.parse(DEVICE.read_text())
  return tree, next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DeviceLayout')


def method(cls, name):
  return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


class Harness:
  def __init__(self):
    self.pushed, self.logged = [], []
    tree, cls = device_class()
    fn = method(cls, '_open_dashcam')
    module = ast.Module(body=[fn], type_ignores=[])
    ns = dict(gui_app=SimpleNamespace(push_widget=self.pushed.append),
              cloudlog=SimpleNamespace(exception=self.logged.append),
              alert_dialog=lambda text: ('alert', text))
    exec(compile(module, str(DEVICE), 'exec'), ns)
    self.open = lambda: ns['_open_dashcam'](object())


class BrokenAtImport:
  """Import finder whose module raises while it is being executed (a syntax error, a missing library, a bad file...)."""

  def __init__(self, exc):
    self.exc = exc

  def find_spec(self, name, path, target=None):
    if name == MODULE:
      import importlib.util
      exc = self.exc

      class Loader:
        def create_module(self, spec):
          return None

        def exec_module(self, module):
          raise exc
      return importlib.util.spec_from_loader(name, Loader())
    return None


@pytest.mark.parametrize('exc', [ImportError('no module'), OSError('disk gone'), RuntimeError('boom'), SyntaxError('bad file'), ValueError('x')])
def test_a_dashcam_module_that_fails_to_import_only_shows_a_notice(monkeypatch, exc):
  monkeypatch.delitem(sys.modules, MODULE, raising=False)
  monkeypatch.setattr(sys, 'meta_path', [BrokenAtImport(exc), *sys.meta_path])
  h = Harness()
  h.open()          # must not raise
  assert len(h.logged) == 1 and h.pushed == [('alert', 'Dashcam unavailable. Driving is unaffected.')]


def test_a_module_missing_entirely_only_shows_a_notice(monkeypatch):
  monkeypatch.setitem(sys.modules, MODULE, None)       # `from x import y` raises ImportError
  h = Harness()
  h.open()
  assert h.pushed == [('alert', 'Dashcam unavailable. Driving is unaffected.')]


def test_a_widget_that_fails_to_construct_only_shows_a_notice(monkeypatch):
  fake = ModuleType(MODULE)

  def boom():
    raise RuntimeError('texture, font or thread failure')
  fake.DashcamLayout = boom
  monkeypatch.setitem(sys.modules, MODULE, fake)
  h = Harness()
  h.open()
  assert h.pushed == [('alert', 'Dashcam unavailable. Driving is unaffected.')] and len(h.logged) == 1


def test_a_healthy_module_is_pushed(monkeypatch):
  fake = ModuleType(MODULE)
  fake.DashcamLayout = lambda: 'widget'
  monkeypatch.setitem(sys.modules, MODULE, fake)
  h = Harness()
  h.open()
  assert h.pushed == ['widget'] and not h.logged


def test_device_py_never_imports_the_dashcam_module_at_load_time_and_gates_the_button_offroad():
  tree, cls = device_class()
  for node in tree.body:
    if isinstance(node, ast.Import | ast.ImportFrom):
      names = [a.name for a in node.names] + [getattr(node, 'module', None) or '']
      assert not any('dashcam' in n for n in names), 'device.py must import the Dashcam screen lazily'
  src = DEVICE.read_text()
  assert "button_item('Dashcam', 'VIEW', callback=self._open_dashcam, enabled=ui_state.is_offroad)" in src
  fn = method(cls, '_open_dashcam')
  tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
  assert len(tries) == 1 and any(isinstance(x, ast.ImportFrom) for x in ast.walk(tries[0]))
  assert [ast.unparse(h.type) for h in tries[0].handlers] == ['Exception']


# ---- structure ---------------------------------------------------------------------------------------------------------------

def imports_of(path):
  names = []
  for node in ast.walk(ast.parse(path.read_text())):
    if isinstance(node, ast.Import):
      names += [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
      names.append(node.module or '')
  return names


@pytest.mark.parametrize('path', sorted(PLAYER.glob('*.py')), ids=lambda p: p.name)
def test_player_logic_imports_no_ui_code(path):
  for name in imports_of(path):
    assert not name.startswith(('pyray', 'raylib', 'openpilot.selfdrive.ui', 'openpilot.system.ui', 'imgui')), (path.name, name)


def calls(node, attr):
  return [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def test_the_widget_keeps_its_fail_closed_structure():
  tree = ast.parse(WIDGET.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DashcamLayout')
  for name in ('_update_state', '_render'):
    fn = method(cls, name)
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries and any([ast.unparse(h.type) for h in t.handlers] == ['Exception'] and calls(t, '_fail') for t in tries), name
  update = ast.unparse(method(cls, '_update_state'))
  assert 'ui_state.started' in update and 'self._close()' in update          # offroad only: closes the moment the car starts
  release = method(cls, '_release')
  assert 'set_override_interactive_timeout(None)' in ast.unparse(release)
  assert 'self._session.close' in ast.unparse(release) and '_unload_texture' in ast.unparse(release)
  assert 'set_override_interactive_timeout' in ast.unparse(method(cls, 'show_event'))
  assert 'self._release()' in ast.unparse(method(cls, 'hide_event'))
  imported = imports_of(WIDGET)
  assert not any('params' in n.lower() for n in imported), 'the Dashcam screen adds no Params keys and reads none'
  assert not any(n.startswith(('cereal.messaging', 'msgq', 'openpilot.system.manager')) for n in imported)


def test_no_blocking_work_in_the_widget_file():
  src = WIDGET.read_text()
  for banned in ('open(', 'os.listdir', 'os.scandir', 'subprocess', 'time.sleep', 'index_video', 'read_video', 'read_qlog', 'read_events', 'setxattr'):
    assert banned not in src, banned


def test_the_widget_does_not_shadow_any_widget_base_method():
  """Widget.render() calls self._layout(), self._update_state() ...; an attribute of the same name would raise inside the base class,
  outside the widget's own guards (this happened once during development)."""
  base_tree = ast.parse((ROOT / 'system/ui/widgets/__init__.py').read_text())
  base = next(n for n in base_tree.body if isinstance(n, ast.ClassDef) and n.name == 'Widget')
  reserved = {n.name for n in base.body if isinstance(n, ast.FunctionDef)}
  cls = next(n for n in ast.parse(WIDGET.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'DashcamLayout')
  assigned = {t.attr for n in ast.walk(cls) if isinstance(n, ast.Assign | ast.AnnAssign)
              for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
              if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == 'self'}
  assert not assigned & reserved, assigned & reserved
