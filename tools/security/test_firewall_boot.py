from contextlib import nullcontext
import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('boot', Path(__file__).with_name('firewall_boot.py'))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


@pytest.fixture
def state(tmp_path, monkeypatch):
  calls = []
  monkeypatch.setattr(boot, 'STATE', tmp_path)
  monkeypatch.setattr(boot.trial, 'trusted', lambda _: None)
  monkeypatch.setattr(boot, 'writable_root', lambda: nullcontext())
  monkeypatch.setattr(boot, 'run', lambda argv, **kw: calls.append(argv))
  monkeypatch.setattr(boot.subprocess, 'check_output', lambda *a, **kw: '*filter\nCOMMIT\n')
  monkeypatch.setattr(boot.trial.firewall, 'apply', lambda: calls.append(['apply']))
  monkeypatch.setattr(boot.trial.firewall, 'verify', lambda: calls.append(['verify']))
  for name, value in [('phase', 'pending'), ('iptables', '*filter\nCOMMIT\n'), ('ip6tables', '*filter\nCOMMIT\n')]:
    (tmp_path / name).write_text(value)
  return tmp_path, calls


def test_pending_requires_timer_first(state):
  _, calls = state
  boot.boot()
  assert calls == [['/usr/bin/systemctl', 'is-active', '--quiet', boot.TIMER], ['apply']]


def test_dead_timer_prevents_apply(state, monkeypatch):
  _, calls = state
  def fail(*a, **kw):
    raise subprocess.CalledProcessError(3, 'systemctl')
  monkeypatch.setattr(boot, 'run', fail)
  with pytest.raises(subprocess.CalledProcessError):
    boot.boot()
  assert ['apply'] not in calls


def test_partial_failure_restores_both(state, monkeypatch):
  _, calls = state
  def fail():
    raise RuntimeError('IPv6')
  monkeypatch.setattr(boot.trial.firewall, 'apply', fail)
  with pytest.raises(RuntimeError, match='IPv6'):
    boot.boot()
  assert boot.phase() == 'restored'
  assert [c[0] for c in calls[1:3]] == ['/usr/sbin/iptables-legacy-restore', '/usr/sbin/ip6tables-legacy-restore']


def test_failed_restore_retries_and_never_reapplies_after_reboot(state, monkeypatch):
  _, calls = state
  original = boot.run
  def fail(argv, **kw):
    calls.append(argv)
    if argv[0] == '/usr/sbin/iptables-legacy-restore':
      raise subprocess.CalledProcessError(1, argv)
  monkeypatch.setattr(boot, 'run', fail)
  with pytest.raises(RuntimeError, match='Incomplete'):
    boot.restore()
  assert boot.phase() == 'rollback'
  assert len(calls) == 2  # second family still attempted, timer not stopped
  monkeypatch.setattr(boot, 'run', original)
  boot.boot()  # fresh invocation using only persistent state, no /run state
  assert boot.phase() == 'restored'
  assert ['apply'] not in calls


def test_confirm_only_after_verify(state):
  _, calls = state
  boot.confirm()
  assert calls[0] == ['verify']
  assert boot.phase() == 'confirmed'
  calls.clear()
  boot.restore()  # already queued timer cannot revert a confirmed trial
  assert calls == [['/usr/bin/systemctl', 'stop', boot.TIMER]]
  boot.boot()
  assert calls[-1] == ['apply']


def test_bad_verification_cannot_confirm(state, monkeypatch):
  def fail():
    raise RuntimeError('bad rules')
  monkeypatch.setattr(boot.trial.firewall, 'verify', fail)
  with pytest.raises(RuntimeError):
    boot.confirm()
  assert boot.phase() == 'pending'


@pytest.mark.parametrize('phase', ['restored', 'rollback', 'nonsense'])
def test_nonpending_cannot_confirm(state, phase):
  directory, _ = state
  (directory / 'phase').write_text(phase)
  with pytest.raises(RuntimeError):
    boot.confirm()


def test_interrupted_phase_write_is_recoverable(state):
  directory, _ = state
  (directory / 'phase.new').write_text('incomplete')
  boot.set_phase('rollback')
  assert boot.phase() == 'rollback'
  assert not (directory / 'phase.new').exists()


def test_restored_boot_does_not_apply(state):
  directory, calls = state
  (directory / 'phase').write_text('restored')
  boot.boot()
  assert ['apply'] not in calls


def test_rearm_requires_unchanged_baseline(state):
  directory, calls = state
  (directory / 'phase').write_text('restored')
  boot.rearm()
  assert boot.phase() == 'pending'
  assert calls == [['/usr/bin/systemctl', 'restart', boot.TIMER],
                   ['/usr/bin/systemctl', 'is-active', '--quiet', boot.TIMER]]


def test_rearm_refuses_stale_snapshot(state):
  directory, calls = state
  (directory / 'phase').write_text('restored')
  (directory / 'ip6tables').write_text('*filter\n-A INPUT -j DROP\nCOMMIT\n')
  with pytest.raises(RuntimeError, match='Baseline changed'):
    boot.rearm()
  assert boot.phase() == 'restored'
  assert not calls


def test_root_remount_restored_on_exception(monkeypatch):
  calls = []
  class ReadOnly:
    f_flag = boot.os.ST_RDONLY
  monkeypatch.setattr(boot.os, 'statvfs', lambda _: ReadOnly())
  monkeypatch.setattr(boot.os, 'sync', lambda: None)
  monkeypatch.setattr(boot, 'run', lambda argv: calls.append(argv))
  with pytest.raises(RuntimeError):
    with boot.writable_root():
      raise RuntimeError('disk failure')
  assert calls == [['/usr/bin/mount', '-o', 'remount,rw', '/'], ['/usr/bin/mount', '-o', 'remount,ro', '/']]


def test_units_do_not_require_driving_process_or_start_before_rollback():
  folder = Path(__file__).with_name('systemd')
  service = (folder / 'comma-cellular-firewall.service').read_text()
  assert 'Before=comma.service lte.service' in service
  assert 'After=local-fs.target netfilter-persistent.service comma-cellular-rollback.timer' in service
  assert 'Requires=' not in service
  assert 'OnActiveSec=5min' in (folder / 'comma-cellular-rollback.timer').read_text()
  assert 'OnUnitActiveSec=' not in (folder / 'comma-cellular-rollback.timer').read_text()
  assert 'Restart=on-failure' in (folder / 'comma-cellular-rollback.service').read_text()
  assert 'RestartSec=30s' in (folder / 'comma-cellular-rollback.service').read_text()
