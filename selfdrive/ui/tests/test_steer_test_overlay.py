import json

from openpilot.selfdrive.ui.onroad.steer_test_overlay import status_text

NOW = 1000.


def write(tmp_path, **over):
  d = {'time': NOW - 1, 'state': 'running', 'block': 3, 'of': 10, 'qualifying': True}
  d.update(over)
  (tmp_path / 'status.json').write_text(json.dumps(d))


def test_counter_text_never_reveals_the_mode(tmp_path):
  write(tmp_path)
  assert status_text(tmp_path, NOW) == 'TEST 3/10'
  write(tmp_path, qualifying=False)
  assert status_text(tmp_path, NOW) == 'TEST 3/10 (paused)'
  write(tmp_path, state='done', block=10)
  assert status_text(tmp_path, NOW) == 'TEST DONE'
  write(tmp_path, mode='B', cfg={'friction': .05})
  assert 'B' not in status_text(tmp_path, NOW)


def test_absent_stale_or_corrupt_status_shows_nothing(tmp_path):
  assert status_text(tmp_path, NOW) is None                     # no experiment armed
  write(tmp_path, time=NOW - 60)
  assert status_text(tmp_path, NOW) is None                     # writer stopped
  write(tmp_path, state='off')
  assert status_text(tmp_path, NOW) is None
  write(tmp_path, block=0)
  assert status_text(tmp_path, NOW) is None
  (tmp_path / 'status.json').write_text('{bad')
  assert status_text(tmp_path, NOW) is None
  write(tmp_path, block='x')
  assert status_text(tmp_path, NOW) is None
