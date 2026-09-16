import subprocess

import pytest

from openpilot.system.hardware.tici import cellular_firewall as fw


def saved_rules():
  return '\n'.join(['*filter', '-A INPUT -j COMMA_CELL_INPUT', '-A INPUT -i wlan0 -j ACCEPT',
                    '-A FORWARD -j COMMA_CELL_FORWARD', *fw.desired_rules(), 'COMMIT'])


def test_scoped_and_both_transports():
  rules = fw.restore_payload()
  assert '-F INPUT' not in rules and '-F FORWARD' not in rules
  assert 'wlan0' not in rules and ':INPUT' not in rules
  assert rules.count('-i ppp+') == 3 and rules.count('-i wwan+') == 3
  assert '-o ppp+ -j DROP' in rules and '-o wwan+ -j DROP' in rules
  fw.verify_rules(saved_rules())


def test_ctstate_order():
  fw.verify_rules(saved_rules().replace('RELATED,ESTABLISHED', 'ESTABLISHED,RELATED'))


def test_ipv6_neighbor_discovery_only():
  rules = fw.desired_rules(ipv6=True)
  nd = [line for line in rules if 'icmp6' in line]
  assert len(nd) == 4
  assert all('--hl-eq 255' in line for line in nd)
  assert all('--icmpv6-type 135' in line or '--icmpv6-type 136' in line for line in nd)
  assert not any('icmp6' in line for line in fw.desired_rules())


@pytest.mark.parametrize('change', ['empty', 'no_hook', 'late_hook', 'missing_drop', 'accept_all', 'foreign_rule'])
def test_verification_catches_ineffective_policy(change):
  text = saved_rules()
  if change == 'empty':
    text = '*filter\nCOMMIT'
  elif change == 'no_hook':
    text = text.replace('-A INPUT -j COMMA_CELL_INPUT\n', '')
  elif change == 'late_hook':
    text = '-A INPUT -j ACCEPT\n' + text
  elif change == 'missing_drop':
    text = text.replace('-A COMMA_CELL_INPUT -i ppp+ -j DROP', '')
  elif change == 'accept_all':
    text = text.replace('-i ppp+ -j DROP', '-i ppp+ -j ACCEPT')
  else:
    text += '\n-A COMMA_CELL_INPUT -j ACCEPT'
  with pytest.raises(RuntimeError):
    fw.verify_rules(text)


def test_ipv6_preflight_failure_never_commits(monkeypatch):
  calls = []
  def run(argv, **kwargs):
    calls.append(argv)
    if 'ip6tables' in argv[0]:
      raise subprocess.CalledProcessError(1, argv)
    return subprocess.CompletedProcess(argv, 0)
  monkeypatch.setattr(fw.subprocess, 'run', run)
  with pytest.raises(subprocess.CalledProcessError):
    fw.apply()
  assert len(calls) == 2
  assert all('--test' in argv for argv in calls)


def test_inspection_error_does_not_insert_hook(monkeypatch):
  calls = []
  def run(argv, **kwargs):
    calls.append(argv)
    return subprocess.CompletedProcess(argv, 2 if '-C' in argv else 0)
  monkeypatch.setattr(fw.subprocess, 'run', run)
  with pytest.raises(RuntimeError, match='inspect'):
    fw.apply()
  assert not any('-I' in argv for argv in calls)
