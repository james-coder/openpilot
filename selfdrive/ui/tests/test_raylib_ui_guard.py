from types import SimpleNamespace

import pytest

from openpilot.selfdrive.ui.tests import test_raylib_ui


def test_dead_ui_cannot_pass_as_initialized(monkeypatch):
  monkeypatch.setattr(test_raylib_ui.messaging, 'recv_one_or_none', lambda sock: None)
  with pytest.raises(AssertionError, match='exited before rendering'):
    test_raylib_ui.wait_for_ui_frame(SimpleNamespace(exitcode=-11), object(), timeout=.01)


def test_no_rendered_frame_cannot_pass(monkeypatch):
  monkeypatch.setattr(test_raylib_ui.messaging, 'recv_one_or_none', lambda sock: None)
  with pytest.raises(AssertionError, match='never rendered'):
    test_raylib_ui.wait_for_ui_frame(SimpleNamespace(exitcode=None), object(), timeout=.01)


def test_rendered_frame_from_live_ui_passes(monkeypatch):
  monkeypatch.setattr(test_raylib_ui.messaging, 'recv_one_or_none', lambda sock: object())
  test_raylib_ui.wait_for_ui_frame(SimpleNamespace(exitcode=None), object(), timeout=.01)
