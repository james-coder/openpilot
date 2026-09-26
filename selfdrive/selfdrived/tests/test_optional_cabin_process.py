import multiprocessing
from types import SimpleNamespace

import pytest
from cereal import log, car
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.selfdrived.events import Events, ET, EventName, OPTIONAL_PROCESSES, driving_process_failures, process_not_running_alert
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.system.manager.process_config import managed_processes
from openpilot.system.manager.process import PythonProcess


def manager_state(*failed):
  return SimpleNamespace(processes=[SimpleNamespace(name=n, running=False, shouldBeRunning=True) for n in failed])


def events_for(state):
  events = Events()
  if driving_process_failures(state):
    events.add(EventName.processNotRunning)
  return events


def test_dead_aranet_does_not_block_engagement_or_disengage():
  events = events_for(manager_state('aranetd'))
  assert not events.contains(ET.NO_ENTRY)
  assert not events.contains(ET.SOFT_DISABLE)
  events.add(EventName.buttonEnable)
  machine = StateMachine()
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.enabled
  for _ in range(500):
    machine.update(events_for(manager_state('aranetd')))
    assert machine.state == log.SelfdriveState.OpenpilotState.enabled


@pytest.mark.parametrize('optional', sorted(OPTIONAL_PROCESSES))
def test_dead_optional_addon_does_not_block(optional):
  assert not driving_process_failures(manager_state(optional))


@pytest.mark.parametrize('name', sorted((set(managed_processes) - OPTIONAL_PROCESSES) | {'unknown_future_process', 'aranatd'}))
def test_every_other_process_still_blocks_and_disengages(name):
  state = manager_state('aranetd', name)
  assert driving_process_failures(state) == {name}
  events = events_for(state)
  events.add(EventName.buttonEnable)
  machine = StateMachine()
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  machine.state = log.SelfdriveState.OpenpilotState.enabled
  machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.softDisabling
  for _ in range(500):
    machine.update(events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  alert = process_not_running_alert(None, None, {'managerState': state}, False, 0, None)
  assert name in alert.alert_text_2
  assert 'aranetd' not in alert.alert_text_2


@pytest.mark.parametrize('running,expected', [(True, True), (False, False), (True, False)])
def test_healthy_or_not_requested_processes_are_not_failures(running, expected):
  state = SimpleNamespace(processes=[SimpleNamespace(name='controlsd', running=running, shouldBeRunning=expected)])
  assert not driving_process_failures(state)


@pytest.fixture
def live_event_path():
  # Isolated IPC/params: no manager launch, vehicle connection, or CAN publisher.
  with OpenpilotPrefix():
    cp = car.CarParams.new_message(brand='gm', carFingerprint='CHEVROLET_VOLT', pcmCruise=True)
    d = SelfdriveD(cp)
    d.initialized = True
    d.startup_event = None
    d.rk = SimpleNamespace(lagging=False)
    d.car_events = SimpleNamespace(update=lambda *args: Events())
    for name in d.sm.services:
      if name != 'pandaStates':
        d.sm.data[name] = d.sm.data[name].as_builder()
      d.sm.alive[name] = d.sm.valid[name] = d.sm.freq_ok[name] = True
      d.sm.updated[name] = False
      d.sm.recv_frame[name] = 100
    d.sm.frame = 100
    d.sm.recv_frame['lateralManeuverPlan'] = d.sm.recv_frame['alertDebug'] = 0
    d.sm.data['deviceState'].freeSpacePercent = 50
    d.sm.data['liveCalibration'].calStatus = log.LiveCalibrationData.Status.calibrated
    d.sm.data['livePose'].posenetOK = True
    d.sm.data['livePose'].inputsOK = True
    d.sm.data['liveParameters'].valid = True
    cs = car.CarState.new_message(canValid=True, vEgo=20.)
    yield d, cs


@pytest.mark.parametrize('failure', ['absent', 'stopped', 'crashed', 'permission_denied', 'cold_boot_unavailable'])
def test_real_event_path_optional_failure(live_event_path, failure):
  d, cs = live_event_path
  msg = log.ManagerState.new_message()
  if failure != 'absent':
    proc = msg.init('processes', 1)[0]
    proc.name = 'aranetd'
    proc.running = False
    proc.shouldBeRunning = failure != 'stopped'
    proc.exitCode = 1 if failure in ('permission_denied', 'crashed') else 0
  d.sm.data['managerState'] = msg
  d.update_events(cs)
  assert EventName.processNotRunning not in d.events.names
  assert not d.events.contains(ET.NO_ENTRY), d.events.names
  d.events.add(EventName.buttonEnable)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  for _ in range(500):
    d.update_events(cs)
    d.state_machine.update(d.events)
    assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled


@pytest.mark.parametrize('fault,event', [('controlsd', EventName.processNotRunning),
                                       ('camera', EventName.cameraMalfunction),
                                       ('can', EventName.canBusMissing)])
def test_real_event_path_other_faults_remain_blocking(live_event_path, fault, event):
  d, cs = live_event_path
  d.sm.data['managerState'] = manager_state('aranetd', 'controlsd') if fault == 'controlsd' else manager_state('aranetd')
  if fault == 'camera':
    d.sm.alive['roadCameraState'] = False
  if fault == 'can':
    cs.canTimeout = True
  d.update_events(cs)
  assert event in d.events.names
  d.events.add(EventName.buttonEnable)
  d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.disabled
  d.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
  for _ in range(500):
    d.update_events(cs)
    d.state_machine.update(d.events)
  assert d.state_machine.state == log.SelfdriveState.OpenpilotState.disabled


def test_old_filter_reproduces_original_engagement_failure(live_event_path, monkeypatch):
  d, cs = live_event_path
  monkeypatch.setattr('openpilot.selfdrive.selfdrived.selfdrived.driving_process_failures',
                      lambda state: {p.name for p in state.processes if not p.running and p.shouldBeRunning})
  d.sm.data['managerState'] = manager_state('aranetd')
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


def permission_denied_collector():
  from openpilot.selfdrive.car import aranet

  def denied():
    raise PermissionError('injected scheduling permission failure; no hardware accessed')

  aranet.background_priority = denied
  aranet.main()


def test_real_collector_startup_failure_and_manager_state(live_event_path):
  d, cs = live_event_path
  process = PythonProcess('aranetd', 'selfdrive.car.aranet', lambda *args: True)
  process.proc = multiprocessing.get_context('spawn').Process(target=permission_denied_collector)
  try:
    process.proc.start()
    process.proc.join(timeout=10)
    assert not process.proc.is_alive(), 'fault-injected collector did not exit'
    state = process.get_process_state_msg()
    assert state.exitCode != 0
    assert not state.running
    assert state.shouldBeRunning
    msg = log.ManagerState.new_message()
    msg.init('processes', 1)
    msg.processes[0] = state
    d.sm.data['managerState'] = msg
    d.update_events(cs)
    assert not d.events.contains(ET.NO_ENTRY), d.events.names
    d.events.add(EventName.buttonEnable)
    d.state_machine.update(d.events)
    assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
    for _ in range(500):
      d.update_events(cs)
      d.state_machine.update(d.events)
      assert d.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  finally:
    if process.proc.is_alive():
      process.proc.kill()
      process.proc.join(timeout=5)
    process.proc.close()
