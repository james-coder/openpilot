import multiprocessing

import pytest
from cereal import log

from openpilot.selfdrive.selfdrived.events import ET, EventName
from openpilot.selfdrive.selfdrived.tests.test_optional_cabin_process import live_event_path  # noqa: F401
from openpilot.system.manager.process_config import managed_processes


def denied_collector():
  from pathlib import Path
  from openpilot.system.hardware import thermal_history
  thermal_history.ROOT = Path('/sys/thermal-history-permission-test')
  thermal_history.main()


@pytest.mark.parametrize('failure', ['absent', 'stopped', 'crashed', 'permission_denied', 'cold_boot_unavailable'])
def test_recorder_failure_does_not_enter_manager_health(live_event_path, failure):  # noqa: F811 -- imported pytest fixture
  d, cs = live_event_path
  assert not any('thermal_history' in str(vars(p)) for p in managed_processes.values())
  if failure == 'permission_denied':
    ctx = multiprocessing.get_context('fork')
    process = ctx.Process(target=denied_collector)
    process.start()
    process.join(5)
    if process.is_alive():
      process.kill()
      process.join()
      pytest.fail('Permission-denied collector did not exit')
    assert process.exitcode != 0
  # systemd failures do not add processes to managerState. No new exemption.
  d.sm.data['managerState'] = log.ManagerState.new_message()
  d.update_events(cs)
  assert not d.events.contains(ET.NO_ENTRY)
  d.events.add(EventName.buttonEnable)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  for _ in range(100):
    d.update_events(cs)
    d.state_machine.update(d.events)
    assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  d.events.add(EventName.buttonCancel)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.disabled
