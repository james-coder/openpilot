"""Validate bounded, ordered evidence from the two disposable USB VM boots."""
import argparse
import json
from pathlib import Path

DRIVERS = ['option'] * 4 + ['qmi_wwan']


def records(text, marker):
  if len(text) > 1024 * 1024 or text.count(marker) != 1:
    raise ValueError('Incomplete or oversized evidence')
  if any(token in text for token in ('BUG:', 'WARNING:', 'Oops:', 'Kernel panic', 'Traceback', 'BINDING_LAB_FAILED', 'MODULE_FAILURE')):
    raise ValueError('Kernel or test failure')
  rows = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
  if any(row.get('status') != 'pass' for row in rows):
    raise ValueError('Failed case')
  return rows


def validate(policy, controls):
  rows = records(policy, 'BINDING_LAB_DONE')
  expected = []
  for cycle in range(10):
    expected.extend([(f'cycle_{cycle}_normal_before', 0, DRIVERS),
                     (f'cycle_{cycle}_modified', 1 + cycle % 7, []),
                     (f'cycle_{cycle}_normal_after', 0, DRIVERS)])
  for action in ('absent', 'crash', 'interrupt'):
    expected.extend([(action, 0, DRIVERS if action == 'crash' else []), (action + '_recovery', 0, DRIVERS)])
  if [(r.get('case'), r.get('mode'), r.get('drivers')) for r in rows] != expected:
    raise ValueError('Missing, reordered, or incorrect policy/binding cases')
  positive = records(controls, 'POSITIVE_CONTROLS_DONE')
  if [(r.get('case'), r.get('drivers')) for r in positive] != [
    ('positive_keyboard', ['usbhid']), ('positive_mouse', ['usbhid']),
    ('positive_storage', ['usb-storage']), ('positive_ethernet', ['cdc_ether', 'cdc_ether']),
  ]:
    raise ValueError('Functional positive controls missing')
  return len(rows) + len(positive)


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('policy', type=Path)
  parser.add_argument('controls', type=Path)
  args = parser.parse_args()
  for path in (args.policy, args.controls):
    if path.stat().st_size > 1024 * 1024:
      parser.error('Evidence too large')
  print(f'{validate(args.policy.read_text(), args.controls.read_text())} cases passed')
