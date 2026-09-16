import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from openpilot.system.hardware.tici import modem
from openpilot.system.hardware.tici.modem_recovery import PPPProgress, RetryPolicy


def load_helper(name):
  path = Path(__file__).resolve().parents[3] / 'tools/security/modem_recovery' / (name + '.py')
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_backoff_and_stable_reset():
  p = RetryPolicy()
  for now, delay in [(0, 5), (10, 15), (30, 60), (100, 60)]:
    p.failed(now, 'ppp_exit')
    assert not p.ready(now + delay - .1)
    assert p.ready(now + delay)
  p.stable(200)
  p.stable(499)
  assert p.failures == 4
  p.stable(500)
  assert p.failures == 0


def test_only_repeated_registered_early_hangups_can_escalate():
  p = RetryPolicy()
  for now in (0, 15, 30):
    p.failed(now, 'registered_early_hangup')
  assert not p.recovery_due(59)
  assert p.recovery_due(60)
  p.failed(61, 'ppp_exit')
  assert not p.recovery_due(999)


def test_progress_bounded_fragmented_and_no_overlong_tail_marker():
  p = PPPProgress()
  p.feed(b'x' * 4096 + b'CHAP authentication succeeded\n')
  assert not p.authenticated and not p.pending
  p.feed(b'CHAP authentication ')
  p.feed(b'succeeded\n')
  assert p.authenticated
  p.feed(b'local  IP address 10.0.0.1\n')
  assert p.address_assigned
  p.feed(b'x' * 100000)
  assert len(p.pending) <= 512


@pytest.fixture
def worker(monkeypatch):
  m = modem.Modem()
  now = [100.0]
  monkeypatch.setattr(modem.time, 'monotonic', lambda: now[0])
  monkeypatch.setattr(m, '_publish_state', lambda **values: m.S.update(values))
  monkeypatch.setattr(m, '_params_changed', lambda: False)
  monkeypatch.setattr(modem.os.path, 'exists', lambda _: True)
  monkeypatch.setattr(m, '_atv', lambda *_: '2,5')
  monkeypatch.setattr(m._ppp, 'drain_progress', lambda: None)
  monkeypatch.setattr(m._ppp, 'cleanup_routes', lambda: None)
  monkeypatch.setattr(m._ppp, 'reset_data_port', lambda: None)
  monkeypatch.setattr(m._ppp, 'kill', lambda: setattr(m._ppp, '_proc', None))
  monkeypatch.setattr(m, '_poll_iface', lambda: {'connected': False, 'ip_address': ''})
  starts = []
  def start():
    starts.append(now[0])
    m._ppp._proc = SimpleNamespace(poll=lambda: None)
    m._ppp.started_at = now[0]
  monkeypatch.setattr(m._ppp, 'start', start)
  return m, now, starts


def test_no_false_connected_or_duplicate_ppp(worker, monkeypatch):
  m, now, starts = worker
  assert m._do_connecting() == modem.State.CONNECTING
  now[0] += 10
  assert m._do_connecting() == modem.State.CONNECTING
  assert starts == [100]
  monkeypatch.setattr(m, '_poll_iface', lambda: {'connected': True, 'ip_address': '10.0.0.1'})
  assert m._do_connecting() == modem.State.CONNECTED


def test_stuck_negotiation_times_out_without_hardware_recovery(worker):
  m, now, starts = worker
  m._do_connecting()
  now[0] = 221
  assert m._do_connecting() == modem.State.DISCONNECTING
  assert m._retry.reason == 'negotiation_timeout'
  assert not m._retry.recovery_due(999)


@pytest.mark.parametrize('code,auth,address,registration,expected', [
  (16, True, False, '2,5', 'registered_early_hangup'),
  (16, False, False, '2,5', 'ppp_exit'),
  (16, True, True, '2,5', 'ppp_exit'),
  (16, True, False, '2,2', 'ppp_exit'),
  (19, True, False, '2,5', 'ppp_exit'),
])
def test_hangup_evidence(worker, monkeypatch, code, auth, address, registration, expected):
  m, _, _ = worker
  m._ppp._proc = SimpleNamespace(poll=lambda: code)
  m._ppp.started_at = 99
  m._ppp.progress.authenticated = auth
  m._ppp.progress.address_assigned = address
  monkeypatch.setattr(m, '_atv', lambda *_: registration)
  assert m._handle_pppd_exit() == modem.State.DISCONNECTING
  assert m._retry.reason == expected
  assert m.S['ppp_exit_status'] == code


def test_disconnect_does_not_erase_failure_history(worker):
  m, _, starts = worker
  m._retry.failed(100, 'ppp_exit')
  assert m._do_disconnecting() == modem.State.INITIALIZING
  assert m._do_connecting() == modem.State.CONNECTING
  assert not starts and m._retry.failures == 1


@pytest.mark.parametrize('exit_status,expected', [(0, 'reset_requested'), (2, 'deferred'), (3, 'budget_exhausted'), (1, 'failed')])
def test_helper_outcomes_never_crash_worker(worker, monkeypatch, exit_status, expected):
  m, _, _ = worker
  for t in (0, 10, 20):
    m._retry.failed(t, 'registered_early_hangup')
  monkeypatch.setattr(modem.os.path, 'isfile', lambda _: True)
  monkeypatch.setattr(modem.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=exit_status))
  assert m._try_recovery(100) == (exit_status == 0)
  assert m.S['recovery_status'] == expected


@pytest.mark.parametrize('error', [PermissionError(), subprocess.TimeoutExpired('helper', 110)])
def test_helper_unavailable_or_crashed(worker, monkeypatch, error):
  m, _, _ = worker
  for t in (0, 10, 20):
    m._retry.failed(t, 'registered_early_hangup')
  monkeypatch.setattr(modem.os.path, 'isfile', lambda _: True)
  def fail(*a, **kw):
    raise error
  monkeypatch.setattr(modem.subprocess, 'run', fail)
  assert not m._try_recovery(100)
  assert m.S['recovery_status'] == 'failed'


def test_missing_helper(worker, monkeypatch):
  m, _, _ = worker
  for t in (0, 10, 20):
    m._retry.failed(t, 'registered_early_hangup')
  monkeypatch.setattr(modem.os.path, 'isfile', lambda _: False)
  assert not m._try_recovery(100)
  assert m.S['recovery_status'] == 'unavailable'


def test_connected_state_reports_recovery_and_stable_reset(worker):
  m, _, _ = worker
  m._retry.failed(0, 'ppp_exit')
  m.S['recovery_status'] = 'reset_requested'
  m._record_connected(100)
  assert m.S['recovery_status'] == 'reconnected'
  assert m.S['retry_count'] == 1
  m._record_connected(400)
  assert m.S['retry_count'] == 0
  assert m.S['retry_reason'] == 'none'


def test_coverage_lost_before_recovery(worker, monkeypatch):
  m, _, _ = worker
  for t in (0, 10, 20):
    m._retry.failed(t, 'registered_early_hangup')
  monkeypatch.setattr(m, '_atv', lambda *_: '2,2')
  monkeypatch.setattr(modem.subprocess, 'run', lambda *a, **kw: pytest.fail('must not reset'))
  assert not m._try_recovery(100)


def test_initializer_waits_for_hardware_without_touching_ports(worker, monkeypatch):
  m, _, _ = worker
  monkeypatch.setattr(m, '_lte_initialized', lambda: False)
  monkeypatch.setattr(m._ppp, 'kill', lambda: pytest.fail('port touched during hardware startup'))
  assert m._do_initializing() == modem.State.INITIALIZING


@pytest.mark.parametrize('state,substate,result,ready', [('active', 'running', 'success', False),
                                                       ('active', 'exited', 'success', True),
                                                       ('failed', 'failed', 'exit-code', False),
                                                       ('inactive', 'dead', 'success', False)])
def test_systemd_active_is_not_hardware_ready(monkeypatch, state, substate, result, ready):
  monkeypatch.setattr(modem.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
    returncode=0, stdout=f'ActiveState={state}\nSubState={substate}\nResult={result}\n'))
  assert modem.Modem._lte_initialized() == ready


def test_permission_error_during_initialization_keeps_worker_alive(worker, monkeypatch):
  m, _, _ = worker
  attempts = []
  def initialize():
    attempts.append(1)
    raise PermissionError('denied')
  monkeypatch.setattr(m, '_do_initializing', initialize)
  monkeypatch.setattr(m, '_check_iccid', lambda *_: None)
  def tick(_):
    if len(attempts) == 2:
      m.running = False
  monkeypatch.setattr(modem.time, 'sleep', tick)
  m.run()
  assert len(attempts) == 2


@pytest.mark.parametrize('routes,dns', [(False, False), (True, False), (True, True)])
def test_link_requires_successful_routes_and_dns(monkeypatch, routes, dns):
  m = modem.Modem()
  monkeypatch.setattr(modem.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='inet 10.0.0.2 peer 10.0.0.1/32'))
  monkeypatch.setattr(m._ppp, 'maybe_install_routes', lambda *a: routes)
  monkeypatch.setattr(m._ppp, 'maybe_install_dns', lambda *a: dns)
  monkeypatch.setattr(m, '_read_cellular_dns', lambda: ['8.8.8.8'])
  assert m._poll_iface()['connected'] == (routes and dns)


def test_cleanup_has_finite_rule_limit(monkeypatch):
  calls = []
  def run(argv, **kw):
    calls.append(argv)
    assert kw['timeout'] <= 5
    return SimpleNamespace(returncode=0)
  monkeypatch.setattr(modem.subprocess, 'run', run)
  with pytest.raises(RuntimeError, match='limit'):
    modem.PPPSession.cleanup_routes()
  assert len(calls) == 18


def test_broker_budget_survives_new_invocation_and_failure(tmp_path, monkeypatch):
  b = load_helper('recover')
  monkeypatch.setattr(b, 'STATE', tmp_path / 'budget')
  monkeypatch.setattr(b, 'trusted', lambda _: None)
  monkeypatch.setattr(b.os, 'geteuid', lambda: 0)
  monkeypatch.setattr(b, 'safe', lambda: True)
  calls = []
  def fail(argv, **kw):
    calls.append(argv)
    raise subprocess.TimeoutExpired(argv, 5)
  monkeypatch.setattr(b.subprocess, 'run', fail)
  with pytest.raises(subprocess.TimeoutExpired):
    b.recover()
  assert b.recover() == 3
  assert calls == [['/usr/bin/systemctl', '--no-block', 'restart', 'lte.service']]


def test_broker_unsafe_does_not_consume_budget(tmp_path, monkeypatch):
  b = load_helper('recover')
  monkeypatch.setattr(b, 'STATE', tmp_path / 'budget')
  monkeypatch.setattr(b, 'trusted', lambda _: None)
  monkeypatch.setattr(b.os, 'geteuid', lambda: 0)
  monkeypatch.setattr(b, 'safe', lambda: False)
  assert b.recover() == 2
  assert not (b.STATE / 'attempted').exists()


@pytest.mark.parametrize('bad', ['none', 'started', 'ignition', 'controls', 'empty_pandas', 'gps_running', 'gps_expected',
                                'gps_absent', 'stale', 'invalid', 'unseen', 'future'])
def test_hardware_reset_safety_gate(bad):
  gate = load_helper('check_offroad')
  class SM(dict):
    pass
  sm = SM(deviceState=SimpleNamespace(started=bad == 'started'),
          pandaStates=[SimpleNamespace(ignitionLine=bad == 'ignition', ignitionCan=False, controlsAllowed=bad == 'controls')],
          managerState=SimpleNamespace(processes=[SimpleNamespace(name='qcomgpsd', running=bad == 'gps_running',
                                                                 shouldBeRunning=bad == 'gps_expected')]))
  sm.seen = dict.fromkeys(sm, bad != 'unseen')
  sm.valid = dict.fromkeys(sm, bad != 'invalid')
  sm.recv_time = dict.fromkeys(sm, 0 if bad == 'stale' else 101 if bad == 'future' else 99.5)
  if bad == 'empty_pandas':
    sm['pandaStates'] = []
  if bad == 'gps_absent':
    sm['managerState'].processes = []
  assert gate.observation_safe(sm, 100) == (bad == 'none')
