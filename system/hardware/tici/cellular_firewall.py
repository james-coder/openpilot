"""Scoped legacy-netfilter rules. No automatic installation or manager dependency.

Default invocation prints candidate rules. --apply/--verify require root and are
for a separately gated installer/boot service; they do not change USB or routing.
"""
import argparse
import shlex
import subprocess

CHAINS = ('COMMA_CELL_INPUT', 'COMMA_CELL_FORWARD')


def desired_rules(ipv6=False):
  lines = []
  for iface in ('ppp+', 'wwan+'):
    if ipv6:
      # Required for fresh/recreated Ethernet-style WWAN links. No router
      # advertisements, redirects, echo requests or arbitrary ICMPv6 exception.
      for kind in (135, 136):
        lines.append(f'-A {CHAINS[0]} -i {iface} -p ipv6-icmp -m hl --hl-eq 255 -m icmp6 --icmpv6-type {kind} -j ACCEPT')
    lines.extend([f'-A {CHAINS[0]} -i {iface} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT',
                  f'-A {CHAINS[0]} -i {iface} -j DROP',
                  f'-A {CHAINS[1]} -i {iface} -j DROP',
                  f'-A {CHAINS[1]} -o {iface} -j DROP'])
  return lines


def restore_payload(ipv6=False):
  return '\n'.join(['*filter', *[f':{c} - [0:0]' for c in CHAINS],
                    *[f'-F {c}' for c in CHAINS], *desired_rules(ipv6), 'COMMIT', ''])


def normalize(line):
  tokens = shlex.split(line)
  if '--ctstate' in tokens:
    pos = tokens.index('--ctstate') + 1
    tokens[pos] = ','.join(sorted(tokens[pos].split(',')))
  return tokens


def verify_rules(saved, ipv6=False):
  lines = [line for line in saved.splitlines() if line.startswith('-A ')]
  for base, chain in zip(('INPUT', 'FORWARD'), CHAINS, strict=True):
    base_rules = [line for line in lines if line.startswith(f'-A {base} ')]
    if not base_rules or normalize(base_rules[0]) != ['-A', base, '-j', chain]:
      raise RuntimeError(f'Missing first-position {chain} hook')
    actual = [normalize(line) for line in lines if line.startswith(f'-A {chain} ')]
    expected = [normalize(line) for line in desired_rules(ipv6) if line.startswith(f'-A {chain} ')]
    if actual != expected:
      raise RuntimeError(f'Unexpected contents of {chain}')


def verify():
  for prefix in ('iptables', 'ip6tables'):
    saved = subprocess.check_output([f'/usr/sbin/{prefix}-legacy-save', '-t', 'filter'], text=True, timeout=10)
    verify_rules(saved, ipv6=prefix == 'ip6tables')


def apply():
  # Check both families before changing either. This is not a substitute for the
  # installer's rollback timer: IPv4 and IPv6 commits are separate transactions.
  for prefix in ('iptables', 'ip6tables'):
    subprocess.run([f'/usr/sbin/{prefix}-legacy-restore', '--test', '--noflush'],
                   input=restore_payload(prefix == 'ip6tables'), text=True, check=True, timeout=10)
  for prefix in ('iptables', 'ip6tables'):
    subprocess.run([f'/usr/sbin/{prefix}-legacy-restore', '--noflush'],
                   input=restore_payload(prefix == 'ip6tables'), text=True, check=True, timeout=10)
    executable = f'/usr/sbin/{prefix}-legacy'
    for base, chain in zip(('INPUT', 'FORWARD'), CHAINS, strict=True):
      # Refuse to silently reorder a foreign ruleset. A fresh install inserts
      # our hook; a pre-existing hook must already be first (verified below).
      result = subprocess.run([executable, '-w', '5', '-C', base, '-j', chain], capture_output=True, timeout=10)
      if result.returncode == 1:
        subprocess.run([executable, '-w', '5', '-I', base, '1', '-j', chain], check=True, timeout=10)
      elif result.returncode:
        raise RuntimeError('Unable to inspect firewall hook')
  verify()


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  actions = parser.add_mutually_exclusive_group()
  actions.add_argument('--apply', action='store_true')
  actions.add_argument('--verify', action='store_true')
  args = parser.parse_args()
  if args.apply:
    apply()
  elif args.verify:
    verify()
  else:
    print(restore_payload(), end='')
