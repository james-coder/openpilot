"""Inventory stable changes for manual provenance review, NOT a vulnerability scanner.

Use separate blob-filtered history repositories. Never changes a kernel checkout.
Output deliberately leaves classification unresolved: absence of a matching SHA
does not establish absence of an equivalent Qualcomm/vendor backport.
"""
import argparse
import json
from pathlib import Path
import subprocess

PATHS = ['drivers/usb', 'drivers/tty', 'drivers/net/ppp', 'net', 'mm', 'lib', 'security',
         'include/linux/usb', 'include/linux/skbuff.h', 'include/linux/slab.h', 'kernel', 'arch/arm64']


def git(repo, *args):
  return subprocess.check_output(['git', '-C', repo, *args], text=True)


def inventory(stable, vendor, vendor_revision):
  if git(stable, 'rev-parse', '--is-shallow-repository').strip() != 'false':
    raise RuntimeError('Complete stable history required')
  if git(vendor, 'rev-parse', '--is-shallow-repository').strip() != 'false':
    raise RuntimeError('Complete vendor history required; shallow absence is not evidence')
  base = git(stable, 'rev-parse', 'v4.9.103^{commit}').strip()
  end = git(stable, 'rev-parse', 'v4.9.337^{commit}').strip()
  target = git(vendor, 'rev-parse', vendor_revision + '^{commit}').strip()
  history = git(stable, 'log', '--reverse', '--format=%H%x09%s', f'{base}..{end}', '--', *PATHS)
  rows = []
  for line in history.splitlines():
    sha, subject = line.split('\t', 1)
    rows.append(dict(stable_commit=sha, subject=subject, classification='unresolved',
                     vendor_evidence=[], prerequisites=[], reachability='unreviewed', tests=[],
                     source=f'https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/commit/?id={sha}'))
  return dict(schema=1, complete_security_audit=False, stable_base=base, stable_end=end,
              vendor_revision=target, paths=PATHS, candidate_count=len(rows), fixes=rows)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--stable', required=True)
  parser.add_argument('--vendor', required=True)
  parser.add_argument('--vendor-revision', default='c368754c26c7b9659de187addc6cccedc6cfb0a0')
  parser.add_argument('--output', type=Path, help='Generated JSON evidence; refuses overwriting an existing file')
  args = parser.parse_args()
  data = json.dumps(inventory(args.stable, args.vendor, args.vendor_revision), indent=2)
  if args.output:
    with args.output.open('x') as stream:
      stream.write(data + '\n')
  else:
    print(data)
