"""Validate bounded disposable-VM serial output; never run/modify a USB device."""
import re


def validate(text):
  if len(text) > 1024*1024:
    raise ValueError('Oversized lab log')
  text = text.replace('\r', '')
  if any(s in text for s in ('LAB_FAILURE', 'LAB_NO_DEVICE', 'UNEXPECTED_SET_CONFIGURATION', 'Kernel panic', 'BUG:', 'Oops:')):
    raise ValueError('Lab failure marker')
  if 'LAB_DONE' not in text:
    raise ValueError('Incomplete lab run')
  cases = re.findall(r'^CASE=(\d+) authorized=(\d+) driver_bindings=(\d+) descriptor_bytes=(\d+)\n(PROFILE_MATCH|PROFILE_REJECT)$', text, re.M)
  if [int(c[0]) for c in cases] != [0, 1, 2, 3, 4, 5, 6, 7, 1, 0]:
    raise ValueError('Missing or unexpected cases')
  for mode, authorized, bindings, size, profile in cases:
    expected_size = 227 if int(mode) in (0, 7) else 236
    if authorized != '0' or bindings != '0' or int(size) != expected_size:
      raise ValueError('Authorization/binding/descriptor mismatch')
    if profile != ('PROFILE_MATCH' if mode == '0' else 'PROFILE_REJECT'):
      raise ValueError('Profile decision mismatch')
  return len(cases)


if __name__ == '__main__':
  import argparse
  from pathlib import Path
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('log', type=Path)
  args = parser.parse_args()
  with args.log.open() as f:
    result = validate(f.read(1024*1024+1))
  print(f'PASS {result} descriptor/default-deny cases; NOT vendor-kernel containment validation')
