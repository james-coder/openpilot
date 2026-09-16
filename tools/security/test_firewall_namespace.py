"""Benign namespace-only integration test. Run in a NEW network namespace.

Refuses the initial namespace. Requires legacy IPv4/IPv6 netfilter support.
Never invoke against the host namespace; no service or interface changes outside
the namespace are needed. Packet/reconnect coverage is a separate test stage.
"""
import os
from pathlib import Path
import subprocess

from openpilot.system.hardware.tici.cellular_firewall import apply, verify


def main():
  if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
    raise RuntimeError('Refusing host network namespace')
  if not Path('/proc/self/ns/net').exists():
    raise RuntimeError('Network namespace unavailable')
  for tool in ('iptables', 'ip6tables'):
    subprocess.run([f'/usr/sbin/{tool}-legacy', '-A', 'INPUT', '-i', 'lo', '-j', 'ACCEPT'], check=True)
  apply()
  apply()
  verify()
  for tool in ('iptables', 'ip6tables'):
    subprocess.run([f'/usr/sbin/{tool}-legacy', '-C', 'INPUT', '-i', 'lo', '-j', 'ACCEPT'], check=True)
    saved = subprocess.check_output([f'/usr/sbin/{tool}-legacy-save', '-t', 'filter'], text=True)
    assert saved.count('-A INPUT -j COMMA_CELL_INPUT\n') == 1
    assert saved.count('-A FORWARD -j COMMA_CELL_FORWARD\n') == 1
  print('PASS: IPv4/IPv6 rules, effective verification, idempotence, unrelated-rule preservation')


if __name__ == '__main__':
  main()
