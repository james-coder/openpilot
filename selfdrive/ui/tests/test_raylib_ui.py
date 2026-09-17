import time
from cereal import messaging
from openpilot.selfdrive.test.helpers import with_processes
from openpilot.system.manager.process_config import managed_processes


def wait_for_ui_frame(proc, sock, timeout=5):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    assert proc.exitcode is None, 'UI exited before rendering a frame'
    if messaging.recv_one_or_none(sock) is not None:
      assert proc.exitcode is None
      return
  raise AssertionError('UI started but never rendered a frame')


@with_processes(["ui"])
def test_raylib_ui():
  """Require a rendered UI frame; process start alone can precede a crash."""
  sock = messaging.sub_sock('uiDebug', timeout=100)
  wait_for_ui_frame(managed_processes['ui'].proc, sock)
