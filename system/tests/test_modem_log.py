import json
from pathlib import Path

from openpilot.system import modem_log as ml


def test_read_state_and_change_detection(tmp_path):
  p = tmp_path / 'modem'
  assert ml.read_state(p) is None
  p.write_text(json.dumps({'state': 'CONNECTED', 'connected': True, 'registration': 'home', 'signal_strength': 60,
                           'seconds_since_boot': 12.5, 'operator': 'X', 'network_type': 'lte'}))
  s = ml.read_state(p)
  assert s['state'] == 'CONNECTED' and s['up'] == 12.5 and s['band'] is None
  assert ml.changed(None, s) and not ml.changed(s, dict(s))
  assert not ml.changed(s, dict(s, signal_strength=65))      # small wobble ignored
  assert ml.changed(s, dict(s, signal_strength=45))          # real drop logged
  assert ml.changed(s, dict(s, connected=False))
  p.write_text('{bad')
  assert ml.read_state(p) is None


def test_append_rotates(tmp_path):
  log = tmp_path / 'modem_events.log'
  ml.append({'t': 1.}, log)
  assert json.loads(log.read_text()) == {'t': 1.0}
  log.write_text('x' * (ml.MAX_BYTES + 1))
  ml.append({'t': 2.}, log)
  assert (tmp_path / 'modem_events.log.1').exists() and json.loads(log.read_text()) == {'t': 2.0}
