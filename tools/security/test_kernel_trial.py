import importlib.util
from contextlib import nullcontext
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('kernel_trial', Path(__file__).with_name('kernel_trial.py'))
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)
legacy = trial
PARSER_SPEC = importlib.util.spec_from_file_location('parser_trial', Path(__file__).with_name('kernel_parser_trial.py'))
parser_trial = importlib.util.module_from_spec(PARSER_SPEC)
PARSER_SPEC.loader.exec_module(parser_trial)


@pytest.fixture(params=[legacy, parser_trial], autouse=True)
def selected_trial(request, monkeypatch, tmp_path):
  monkeypatch.setitem(globals(), 'trial', request.param)
  if request.param is parser_trial:
    monkeypatch.setattr(parser_trial, 'ROOT', tmp_path)
    monkeypatch.setattr(parser_trial, 'boot_id', lambda: 'boot-test')


@pytest.fixture
def sandbox(monkeypatch):
  events = []
  state = {'phase': 'prepared', 'image': trial.BASELINE}
  monkeypatch.setattr(trial, 'writable', nullcontext)
  monkeypatch.setattr(trial, 'manifest', lambda: {'partition_sha256': 'candidate'})
  monkeypatch.setattr(trial, 'hardware', lambda: events.append('hardware'))
  monkeypatch.setattr(trial, 'safe', lambda: events.append('safe'))
  monkeypatch.setattr(trial, 'phase', lambda: state['phase'])
  def set_phase(value):
    events.append(value)
    state['phase'] = value
  monkeypatch.setattr(trial, 'set_phase', set_phase)
  monkeypatch.setattr(trial, 'digest', lambda p: state['image'] if p == trial.TARGET else trial.OTHER_HASH)
  monkeypatch.setattr(trial, 'run', lambda argv, **kw: events.append(argv))
  def write_image(p):
    events.append('write:' + p.name)
    state['image'] = 'candidate' if p.name == 'candidate.img' else trial.BASELINE
  monkeypatch.setattr(trial, 'write_image', write_image)
  return state, events


def test_flash_checks_timer_and_fresh_safety_before_write(sandbox):
  state, events = sandbox
  trial.flash()
  assert events == ['hardware', ['/usr/bin/systemctl', 'stop', trial.TIMER],
                    ['/usr/bin/systemctl', 'enable', '--now', trial.TIMER],
                    ['/usr/bin/systemctl', 'is-active', '--quiet', trial.TIMER],
                    'safe', 'writing', 'write:candidate.img', 'pending']
  assert state['image'] == 'candidate'


@pytest.mark.parametrize('step', ['timer', 'safe', 'hardware', 'image'])
def test_failed_flash_preflight_never_writes(sandbox, monkeypatch, step):
  state, events = sandbox
  def fail(*args, **kwargs):
    raise RuntimeError(step)
  if step == 'image':
    state['image'] = 'unexpected'
  elif step == 'timer':
    monkeypatch.setattr(trial, 'run', fail)
  else:
    monkeypatch.setattr(trial, step, fail)
  with pytest.raises(RuntimeError):
    trial.flash()
  assert not any(isinstance(e, str) and e.startswith('write:') for e in events)


def test_readback_failure_leaves_rollback_armed(sandbox, monkeypatch):
  state, events = sandbox
  monkeypatch.setattr(trial, 'write_image', lambda _: state.update(image='partial'))
  with pytest.raises(RuntimeError, match='Readback'):
    trial.flash()
  assert state['phase'] == 'writing'
  assert events[-1] == 'writing'


@pytest.mark.parametrize('phase,image', [('pending', 'candidate'), ('writing', 'partial'), ('restoring', 'partial')])
def test_rollback_restores_then_verifies_before_reboot(sandbox, phase, image):
  state, events = sandbox
  state.update(phase=phase, image=image)
  trial.rollback()
  assert events == ['hardware', 'safe', 'restoring', 'write:baseline.img', 'restored',
                    ['/usr/bin/systemctl', 'stop', trial.TIMER], 'safe', ['/usr/bin/systemctl', '--no-block', 'reboot']]
  assert state['image'] == trial.BASELINE


def test_unsafe_rollback_neither_writes_nor_reboots(sandbox, monkeypatch):
  state, events = sandbox
  state.update(phase='pending', image='candidate')
  def unsafe():
    raise subprocess.CalledProcessError(2, ['observer'])
  monkeypatch.setattr(trial, 'safe', unsafe)
  with pytest.raises(subprocess.CalledProcessError):
    trial.rollback()
  assert events == ['hardware']


def test_unwritten_trial_deadline_cancels_without_reboot(sandbox):
  state, events = sandbox
  trial.rollback()
  assert state['phase'] == 'cancelled'
  assert events == ['hardware', 'cancelled', ['/usr/bin/systemctl', 'stop', trial.TIMER]]


def test_unknown_image_is_not_overwritten(sandbox):
  state, events = sandbox
  state.update(phase='pending', image='unrelated')
  with pytest.raises(RuntimeError, match='Unknown'):
    trial.rollback()
  assert events == ['hardware']


def test_confirmed_trial_never_restores(sandbox):
  state, events = sandbox
  state['phase'] = 'confirmed'
  trial.rollback()
  assert events == [['/usr/bin/systemctl', 'stop', trial.TIMER]]


def test_rearm_requires_unwritten_cancelled_trial(sandbox):
  state, events = sandbox
  with pytest.raises(RuntimeError):
    trial.rearm()
  state['phase'] = 'cancelled'
  trial.rearm()
  assert state['phase'] == 'prepared'
