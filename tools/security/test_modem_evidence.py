import importlib.util
from pathlib import Path
from struct import pack
from types import SimpleNamespace

import pytest


def load(name):
  spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


diag = load('diag_probe')
observer = load('observe_startup')
lab = load('check_usb_lab_log')


@pytest.mark.parametrize('opcode', [19, 20, 21, 24])
def test_error_never_passes_or_dumps_payload(opcode):
  result = diag.classify(opcode, diag.REQUEST + b'private')
  assert not result['passed'] and result['starts_with_request'] and not result['equals_request']
  assert 'private' not in str(result)


def test_range_and_malformed_replies():
  assert diag.classify(115, pack('<3xII16I', 1, 0, *([2562]*16)))['passed']
  for op, status, bits in [(3, 0, 2562), (1, 1, 2562), (1, 0, 4097)]:
    assert not diag.classify(115, pack('<3xII16I', op, status, *([bits]*16)))['passed']
  for length in [0, 17, 74, 76]:
    assert not diag.classify(115, bytes(length))['passed']


@pytest.mark.parametrize('running,expected', [(True, False), (False, True), (True, True), (False, False)])
def test_diag_gate_respects_gps_ownership(monkeypatch, running, expected):
  monkeypatch.setattr(diag.time, 'monotonic', lambda: 100)
  class SM(dict):
    seen = {'managerState': True}
    valid = {'managerState': True}
    recv_time = {'managerState': 100}
    def update(self, timeout):
      pass
  sm = SM(managerState=SimpleNamespace(processes=[SimpleNamespace(name='qcomgpsd', running=running, shouldBeRunning=expected)]))
  if running or expected:
    with pytest.raises(RuntimeError, match='ownership'):
      diag.gate(sm, lambda: None)
  else:
    diag.gate(sm, lambda: None)
  sm['managerState'].processes = []
  with pytest.raises(RuntimeError, match='ownership'):
    diag.gate(sm, lambda: None)


def test_stale_or_absent_is_not_fresh():
  assert observer.freshness(True, True, 1)
  for args in [(False, True, 0), (True, False, 0), (True, True, 3), (True, True, -1)]:
    assert not observer.freshness(*args)


def test_diag_gate_rechecks_state_after_wait(monkeypatch):
  monkeypatch.setattr(diag.time, 'monotonic', lambda: 100)
  class SM(dict):
    seen = {'managerState': True}
    valid = {'managerState': True}
    recv_time = {'managerState': 100}
    def update(self, timeout):
      pass
  sm = SM(managerState=SimpleNamespace(processes=[SimpleNamespace(name='qcomgpsd', running=False, shouldBeRunning=False)]))
  calls = []
  def offroad():
    calls.append(1)
    if len(calls) == 2:
      raise RuntimeError('Vehicle state changed')
  with pytest.raises(RuntimeError, match='state changed'):
    diag.gate(sm, offroad)
  assert len(calls) == 2


def test_status_bounded_and_private(tmp_path):
  p = tmp_path/'status'
  assert not observer.local_status(p, ['connected'])['available']
  p.write_text('{"connected": true, "imei": "secret"}')
  status = observer.local_status(p, ['connected'])
  assert status['available'] and status['connected'] and status['age_seconds'] < 2
  assert 'imei' not in status
  p.write_text('x'*4097)
  assert not observer.local_status(p, ['connected'])['available']
  p.write_text('[]')
  assert not observer.local_status(p, ['connected'])['available']


def test_lab_log_rejects_incomplete_and_wrong_authorization():
  lines = []
  for mode in [0, 1, 2, 3, 4, 5, 6, 7, 1, 0]:
    size = 227 if mode in (0, 7) else 236
    decision = 'MATCH' if mode == 0 else 'REJECT'
    lines.append(f'CASE={mode} authorized=0 driver_bindings=0 descriptor_bytes={size}\nPROFILE_{decision}')
  text = '\n'.join(lines) + '\nLAB_DONE\n'
  assert lab.validate(text) == 10
  for bad in [text.replace('LAB_DONE', ''), text.replace('authorized=0', 'authorized=1', 1),
              text + 'UNEXPECTED_SET_CONFIGURATION', text.replace('PROFILE_REJECT', 'PROFILE_MATCH', 1)]:
    with pytest.raises(ValueError):
      lab.validate(bad)
