"""Destructive-to-its-own-namespaces packet test, disposable Linux VM ONLY.

Requires root, iproute2, legacy iptables/ip6tables, ping, Python. Creates unique
namespaces; never changes the initial namespace's rules, routes or interfaces.
Run with --disposable-vm and the absolute candidate firewall.py path.
"""
import argparse
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def run(argv, **kwargs):
  return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=20, **kwargs)


def main(path):
  names = [f'cell-lab-{os.getpid()}-{suffix}' for suffix in ('dut', 'peer', 'lan')]
  dut, peer, lan = names
  created = []
  servers = []
  scratch = tempfile.TemporaryDirectory(prefix='firewall-packets-')
  cert, key = str(Path(scratch.name) / 'cert.pem'), str(Path(scratch.name) / 'key.pem')
  endpoint = str(Path(__file__).with_name('packet_endpoint.py').absolute())
  def ns(name, *cmd):
    return run(['ip', 'netns', 'exec', name, *cmd])
  def link(other, iface, subnet, v6):
    ns(dut, 'ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', 'other')
    ns(dut, 'ip', 'link', 'set', 'other', 'netns', other)
    for name, device, host in [(dut, iface, 1), (other, 'other', 2)]:
      ns(name, 'ip', 'addr', 'add', f'{subnet}.{host}/24', 'dev', device)
      ns(name, 'ip', '-6', 'addr', 'add', f'{v6}::{host}/64', 'dev', device, 'nodad')
      ns(name, 'ip', 'link', 'set', device, 'up')
  def ping(name, address, success):
    result = subprocess.run(['ip', 'netns', 'exec', name, 'ping', '-n', '-c', '2', '-W', '3', address],
                            capture_output=True, timeout=10)
    if (result.returncode == 0) != success:
      raise AssertionError(f'{name} -> {address}, expected success={success}: {result.stdout!r} {result.stderr!r}')
  try:
    for name in names:
      run(['ip', 'netns', 'add', name])
      created.append(name)
      ns(name, 'ip', 'link', 'set', 'lo', 'up')
    for other, iface, subnet, v6 in [(peer, 'ppp0', '192.0.2', 'fd00:1'), (lan, 'wlan0', '198.51.100', 'fd00:2')]:
      link(other, iface, subnet, v6)
    ns(dut, 'sysctl', '-w', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.forwarding=1')
    for name, v4, v6 in [(peer, '192.0.2.1', 'fd00:1::1'), (lan, '198.51.100.1', 'fd00:2::1')]:
      ns(name, 'ip', 'route', 'add', 'default', 'via', v4)
      ns(name, 'ip', '-6', 'route', 'add', 'default', 'via', v6)
    for family in ('iptables', 'ip6tables'):
      ns(dut, f'/usr/sbin/{family}-legacy', '-A', 'INPUT', '-i', 'wlan0', '-j', 'ACCEPT')
    for address in ('192.0.2.1', 'fd00:1::1', '198.51.100.2', 'fd00:2::2'):
      ping(peer, address, True)
    for _ in range(2):
      ns(dut, sys.executable, path, '--apply')
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
         '-days', '1', '-subj', '/CN=firewall.test', '-addext', 'subjectAltName=DNS:firewall.test'])
    for name in (dut, peer):
      server = subprocess.Popen(['ip', 'netns', 'exec', name, sys.executable, endpoint, 'server', cert, key],
                                stdout=subprocess.PIPE, text=True)
      servers.append(server)
      if not select.select([server.stdout], [], [], 10)[0] or server.stdout.readline().strip() != 'READY':
        raise RuntimeError('Test endpoint failed to start')
    for address in ('192.0.2.1', 'fd00:1::1', '198.51.100.2', 'fd00:2::2'):
      ping(peer, address, False)
    for address in ('198.51.100.1', 'fd00:2::1'):
      ping(lan, address, True)
      ns(lan, sys.executable, endpoint, 'client', cert, address)
    for address in ('192.0.2.2', 'fd00:1::2'):
      ping(lan, address, False)
    for iface in ('ppp0', 'wwan7'):
      if iface != 'ppp0':
        ns(dut, 'ip', 'link', 'del', 'ppp0')
        link(peer, iface, '192.0.2', 'fd00:1')
        ns(peer, 'ip', 'route', 'add', 'default', 'via', '192.0.2.1')
        ns(peer, 'ip', '-6', 'route', 'add', 'default', 'via', 'fd00:1::1')
      ns(dut, 'ip', '-6', 'neigh', 'flush', 'dev', iface)
      ns(peer, 'ip', '-6', 'neigh', 'flush', 'dev', 'other')
      for address in ('192.0.2.2', 'fd00:1::2'):
        ping(dut, address, True)
        ns(dut, sys.executable, endpoint, 'client', cert, address)
        ns(dut, sys.executable, endpoint, 'related', cert, address)
      for address in ('192.0.2.1', 'fd00:1::1'):
        ping(peer, address, False)
        ns(peer, sys.executable, endpoint, 'blocked', cert, address)
        try:
          ns(peer, sys.executable, endpoint, 'client', cert, address)
        except subprocess.CalledProcessError:
          pass
        else:
          raise AssertionError('Unsolicited inbound DNS was accepted')
    for family in ('iptables', 'ip6tables'):
      ns(dut, f'/usr/sbin/{family}-legacy', '-C', 'INPUT', '-i', 'wlan0', '-j', 'ACCEPT')
    print('PASS: dual-stack unsolicited echo/DNS/TCP and forwarding deny; outbound echo/DNS/HTTPS; RELATED ICMP; Wi-Fi; repeated apply; interface recreation')
    print('NOT COVERED: live timed rollback/two-family failure, target compatibility')
  finally:
    for server in servers:
      server.terminate()
      server.wait(timeout=5)
    for name in reversed(created):
      run(['ip', 'netns', 'del', name])
    scratch.cleanup()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--disposable-vm', action='store_true', required=True)
  parser.add_argument('firewall', type=os.path.abspath)
  args = parser.parse_args()
  main(args.firewall)
