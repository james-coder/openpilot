"""Synthetic real-event-path tests, not vehicle/engagement validation."""
import subprocess
from types import SimpleNamespace

import pytest
from cereal import log
from openpilot.selfdrive.selfdrived.events import ET, EventName
from openpilot.selfdrive.selfdrived.tests.test_optional_cabin_process import live_event_path  # noqa: F401
from openpilot.system.hardware.tici import modem


@pytest.mark.parametrize('failure', ['absent', 'stopped', 'crashed', 'permission_denied', 'cold_boot_unavailable'])
def test_recovery_failure_preserves_real_engagement_event_path(live_event_path, monkeypatch, failure):  # noqa: F811
  m = modem.Modem()
  monkeypatch.setattr(m, '_publish_state', lambda **values: m.S.update(values))
  monkeypatch.setattr(m, '_atv', lambda *_: '2,1')
  monkeypatch.setattr(modem.os.path, 'isfile', lambda _: failure not in ('absent', 'cold_boot_unavailable'))
  def invoke(*args, **kwargs):
    if failure == 'permission_denied':
      raise PermissionError('injected')
    if failure == 'stopped':
      raise subprocess.TimeoutExpired('helper', 110)
    return SimpleNamespace(returncode=1)
  monkeypatch.setattr(modem.subprocess, 'run', invoke)
  for t in (0, 10, 20):
    m._retry.failed(t, 'registered_early_hangup')
  assert not m._try_recovery(100)
  assert m.S['recovery_status'] in ('failed', 'unavailable')

  d, cs = live_event_path
  # The broker is NOT registered with manager. The modem worker survived the
  # actual fault-injected recovery path above; no watchdog exemption is added.
  d.sm.data['managerState'] = SimpleNamespace(processes=[
    SimpleNamespace(name='modem', running=True, shouldBeRunning=True)])
  d.update_events(cs)
  assert not d.events.contains(ET.NO_ENTRY), d.events.names
  d.events.add(EventName.buttonEnable)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  for _ in range(500):
    d.update_events(cs)
    d.state_machine.update(d.events)
    assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled


def test_modem_worker_failure_still_blocks_and_disengages(live_event_path):  # noqa: F811
  d, cs = live_event_path
  d.sm.data['managerState'] = SimpleNamespace(processes=[
    SimpleNamespace(name='modem', running=False, shouldBeRunning=True)])
  d.update_events(cs)
  assert EventName.processNotRunning in d.events.names
  d.events.add(EventName.buttonEnable)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.disabled
  d.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
  for _ in range(500):
    d.update_events(cs)
    d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.disabled
