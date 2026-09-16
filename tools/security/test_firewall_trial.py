from contextlib import nullcontext
import subprocess
import os

import pytest

from openpilot.tools.security import firewall_trial as trial


@pytest.fixture
def lab(tmp_path, monkeypatch):
  calls = []
  original_readlink = os.readlink
  monkeypatch.setattr(trial.os, 'readlink', lambda path, **kwargs: original_readlink('/proc/self/ns/net' if path == '/proc/1/ns/net' else path, **kwargs))
  monkeypatch.setattr(trial, 'trusted', lambda _: None)
  monkeypatch.setattr(trial, 'locked', lambda _: nullcontext())
  monkeypatch.setattr(trial.subprocess, 'check_output', lambda *a, **k: '*filter\nCOMMIT\n')
  monkeypatch.setattr(trial, 'run', lambda argv, **kwargs: calls.append(argv))
  return tmp_path / 'trial', calls


def test_arm_before_apply(lab, monkeypatch):
  directory, calls = lab
  def apply():
    assert calls[-1] == ['systemctl', 'is-active', '--quiet', trial.unit(directory) + '.timer']
    assert (directory / 'iptables').exists() and (directory / 'ip6tables').exists()
    calls.append(['APPLY'])
  monkeypatch.setattr(trial.firewall, 'apply', apply)
  trial.trial(directory)
  assert calls[-1] == ['APPLY']


def test_arm_failure_never_applies(lab, monkeypatch):
  directory, calls = lab
  def run(argv, **kwargs):
    calls.append(argv)
    if argv[0] == 'systemd-run':
      raise subprocess.CalledProcessError(1, argv)
  monkeypatch.setattr(trial, 'run', run)
  monkeypatch.setattr(trial.firewall, 'apply', lambda: pytest.fail('applied without rollback'))
  with pytest.raises(subprocess.CalledProcessError):
    trial.trial(directory)


def test_partial_apply_restores_both(lab, monkeypatch):
  directory, calls = lab
  def apply():
    raise RuntimeError('IPv6 commit failed after IPv4')
  monkeypatch.setattr(trial.firewall, 'apply', apply)
  with pytest.raises(RuntimeError, match='IPv6'):
    trial.trial(directory)
  assert [c[0] for c in calls[-3:-1]] == ['/usr/sbin/iptables-legacy-restore', '/usr/sbin/ip6tables-legacy-restore']
  assert (directory / 'phase').read_text() == 'restored'


def test_restore_attempts_both_and_keeps_retry(lab, monkeypatch):
  directory, calls = lab
  directory.mkdir()
  (directory / 'namespace').write_text(os.readlink('/proc/self/ns/net'))
  for name, value in [('phase', 'pending'), ('iptables', 'v4'), ('ip6tables', 'v6')]:
    (directory / name).write_text(value)
  def run(argv, **kwargs):
    calls.append(argv)
    if 'iptables-legacy' in argv[0]:
      raise subprocess.CalledProcessError(1, argv)
  monkeypatch.setattr(trial, 'run', run)
  with pytest.raises(RuntimeError, match='incomplete'):
    trial.restore(directory)
  assert len(calls) == 2
  assert (directory / 'phase').read_text() == 'pending'


def test_wrong_namespace_refuses_restore(lab):
  directory, calls = lab
  directory.mkdir()
  (directory / 'namespace').write_text('net:[wrong]')
  with pytest.raises(RuntimeError, match='different network'):
    trial.restore(directory)
  assert calls == []


def test_snapshot_comparison_ignores_only_comments_and_counters():
  assert trial.rules_only('# timestamp\n:INPUT ACCEPT [1:2]\n-A INPUT -j ACCEPT') == trial.rules_only(':INPUT ACCEPT [0:0]\n-A INPUT -j ACCEPT')
  assert trial.rules_only('-A INPUT -j ACCEPT') != trial.rules_only('-A INPUT -j DROP')
