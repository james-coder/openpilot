"""Apply an already-computed following-distance-fit.json on this device.

Meant to run ON the comma device itself, after volt_following_distance.py and
volt_following_fit.py have been run elsewhere (extraction/fitting is CPU-heavy and has
no reason to run on the car's own hardware — see their docstrings). This script does
none of that heavy lifting: it only re-validates the already-fitted numbers (bounds,
monotonicity — the exact same check opendbc.car.gm.volt_following.following_profile_valid
runs again at load time, so this is redundant-on-purpose defense in depth, not the only
gate) and writes selfdrive.car.volt_following.PROFILE_PATH. Refuses to write anything if
the report says checks_passed=False.

Writes a plain JSON file, not a Params key: a brand-new Params key needs the compiled
params_pyx C++ extension rebuilt (it hardcodes an allowlist from common/params_keys.h at
compile time), which the normal update/build flow does but a quick deploy does not — and
this value is expected to get hand-tuned repeatedly before real fit data exists. A file
needs no rebuild, ever, for this or any future tweak.
"""

import argparse
import json
import sys
from pathlib import Path

from opendbc.car.gm.volt_following import GapParams, VoltFollowingProfile, following_profile_valid
from openpilot.selfdrive.car.volt_following import PROFILE_PATH


def profile_from_report(report: dict) -> VoltFollowingProfile:
  def gap(level):
    d = report[level]
    return GapParams(t_follow=float(d['t_follow']), comfort_brake=float(d['comfort_brake']), stop_distance=float(d['stop_distance']))
  return VoltFollowingProfile(
    version='volt-following-v1', validated=True, fit_time=report['fit_time'], heldout_rmse_m=float(report['heldout_rmse_m']),
    aggressive=gap('aggressive'), standard=gap('standard'), relaxed=gap('relaxed'),
  )


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('report', type=Path, help='following-distance-fit.json produced by volt_following_fit.py')
  parser.add_argument('--dry-run', action='store_true', help='Validate and print, but do not write the profile file')
  args = parser.parse_args()

  report = json.loads(args.report.read_text())
  if not report.get('checks_passed'):
    print('Refusing: report says checks_passed=False.', file=sys.stderr)
    sys.exit(1)

  profile = profile_from_report(report)
  if not following_profile_valid(profile):
    print('Refusing: bounds/monotonicity re-check failed on this machine, despite checks_passed=True in the report '
          '(the report may be stale or hand-edited).', file=sys.stderr)
    sys.exit(1)

  payload = json.dumps({
    'version': profile.version, 'validated': True, 'fit_time': profile.fit_time, 'heldout_rmse_m': profile.heldout_rmse_m,
    'aggressive': vars(profile.aggressive), 'standard': vars(profile.standard), 'relaxed': vars(profile.relaxed),
  })
  print(payload)
  if args.dry_run:
    print(f'(dry run: not writing {PROFILE_PATH})')
    return
  tmp = PROFILE_PATH.with_suffix('.tmp')
  tmp.write_text(payload)
  tmp.replace(PROFILE_PATH)
  print(f'Wrote validated profile to {PROFILE_PATH}.')


if __name__ == '__main__':
  main()
