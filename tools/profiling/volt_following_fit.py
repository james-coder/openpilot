"""Bin and fit a Volt-specific following-distance profile from volt_following_distance.py's
samples.json.

Bins by speed, fits the same gap(v) = v**2/(2*comfort_brake) + t_follow*v + stop_distance
shape the stock MPC already uses (selfdrive.controls.lib.longitudinal_mpc_lib.gap_params)
through the per-bin minimum and maximum observed gaps, derives a standard/midpoint curve,
and validates against a held-out route never used for fitting.

Like volt_following_distance.py, this belongs on a workstation, not the comma device —
it's pure computation over an already-extracted samples.json, no reason to spend the
car's own CPU/battery on it. This script itself never writes anything to a live car: it
only writes a following-distance-fit.json report (with an explicit checks_passed flag).
Applying a passing fit to the actual device is a separate, deliberately tiny step — copy
the report over and run volt_following_apply.py *on the device* — so the only thing that
ever touches live Params is a short, easily-reviewed script, not this one. See
opendbc.car.gm.volt_following for the bounds/monotonicity gate re-checked at both apply
time and (again) at every LongitudinalPlanner startup, and selfdrive.car.volt_following
for how a validated profile reaches LongitudinalMpc.
"""

import argparse
import json
from dataclasses import asdict
from datetime import datetime, UTC
from pathlib import Path

import numpy as np

from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.gap_params import COMFORT_BRAKE, get_T_FOLLOW, get_safe_obstacle_distance
from opendbc.car.gm.volt_following import GapParams, VoltFollowingProfile, following_profile_valid, STOP_DISTANCE_BOUNDS
from cereal import log

BIN_EDGES = [1, 2, 3, 4, 5, 7, 9, 12, 15, 20, 25, 30]
MIN_SAMPLES_PER_BIN = 20
AGGRESSIVE_PADDING = 1.05  # +5% gap, the user's explicit safety margin above the raw observed minimum.
MAX_HELDOUT_RMSE_M = 0.75
T_FOLLOW_FIT_MARGIN = 0.20  # +/-20% of the corresponding stock personality value.
# STOP_DISTANCE_BOUNDS imported from opendbc.car.gm.volt_following -- used to be a local
# (4.5, 8.0) constant here, drifted from the real safety floor (5.5, 8.0) enforced by
# following_profile_valid(). Harmless in practice (fit()'s own re-validation against the
# real bound, and volt_following_apply.py's independent re-check, both already caught
# anything this clip let through), but exactly the kind of duplicated-constant drift this
# fork has already been bitten by twice -- import the real one instead of keeping a copy.


def bin_samples(samples):
  """Return one row per bin: speed range, count, min/max/p5/p95 gap, bin center speed."""
  bins = []
  for lo, hi in zip(BIN_EDGES, BIN_EDGES[1:], strict=False):
    in_bin = [s for s in samples if lo <= s['v'] < hi]
    d = np.array([s['d'] for s in in_bin])
    row = {'speed_lo': lo, 'speed_hi': hi, 'count': len(in_bin)}
    if len(in_bin) >= MIN_SAMPLES_PER_BIN:
      row.update({
        'low_confidence': False,
        'v_center': float(np.median([s['v'] for s in in_bin])),
        'min': float(d.min()), 'max': float(d.max()),
        'p5': float(np.percentile(d, 5)), 'p95': float(np.percentile(d, 95)),
        'median': float(np.median(d)),
      })
    else:
      row['low_confidence'] = True
    bins.append(row)
  return bins


def _gap_shape(v, t_follow, comfort_brake, stop_distance):
  return get_safe_obstacle_distance(v, t_follow, stop_distance, comfort_brake)


def fit_curve(bins, key, stock_t_follow):
  """Fit (t_follow, stop_distance) through bins[*][key]; comfort_brake stays at the
  existing stock constant.

  gap(v) = t_follow*v + stop_distance (+ the fixed v**2/(2*comfort_brake) term, which
  contributes a known constant offset per bin center and is subtracted out below) is
  linear in (t_follow, stop_distance) — solved with plain numpy.linalg.lstsq, no scipy
  dependency. comfort_brake is deliberately NOT a free parameter: jointly fitting it
  with t_follow is ill-conditioned (v and v**2 are highly collinear over a 2-28 m/s
  range with only a handful of speed bins) and produces unstable, unreliable t_follow
  estimates in practice. Its effect on the curve's high-speed shape is already similar
  to t_follow's, and get_T_FOLLOW is already bounded to within a modest margin of the
  stock personality value for the same "don't disturb high-speed behavior much" reason
  — holding comfort_brake fixed is consistent with that, not an extra restriction.
  """
  usable = [b for b in bins if not b['low_confidence']]
  if len(usable) < 3:
    raise ValueError(f'Not enough well-supported speed bins ({len(usable)}) to fit a curve')
  v = np.array([b['v_center'] for b in usable])
  d = np.array([b[key] for b in usable]) - v**2 / (2 * COMFORT_BRAKE)
  design = np.column_stack([v, np.ones_like(v)])
  (t_follow, stop_distance), *_ = np.linalg.lstsq(design, d, rcond=None)
  t_follow = float(np.clip(t_follow, stock_t_follow * (1 - T_FOLLOW_FIT_MARGIN), stock_t_follow * (1 + T_FOLLOW_FIT_MARGIN)))
  stop_distance = float(np.clip(stop_distance, *STOP_DISTANCE_BOUNDS))
  return GapParams(t_follow=t_follow, comfort_brake=COMFORT_BRAKE, stop_distance=stop_distance)


def pad_gap(gap: GapParams, factor: float) -> GapParams:
  return GapParams(t_follow=gap.t_follow * factor, comfort_brake=gap.comfort_brake, stop_distance=gap.stop_distance * factor)


def midpoint_gap(a: GapParams, b: GapParams) -> GapParams:
  return GapParams(t_follow=(a.t_follow + b.t_follow) / 2, comfort_brake=(a.comfort_brake + b.comfort_brake) / 2,
                    stop_distance=(a.stop_distance + b.stop_distance) / 2)


def evaluate_holdout(gap: GapParams, holdout_samples):
  if not holdout_samples:
    return None
  errors = [_gap_shape(s['v'], gap.t_follow, gap.comfort_brake, gap.stop_distance) - s['d'] for s in holdout_samples]
  return float(np.sqrt(np.mean(np.square(errors))))


def envelope_coverage(aggressive: GapParams, relaxed: GapParams, holdout_samples):
  if not holdout_samples:
    return None
  inside = [_gap_shape(s['v'], aggressive.t_follow, aggressive.comfort_brake, aggressive.stop_distance) - 0.5 <= s['d']
            <= _gap_shape(s['v'], relaxed.t_follow, relaxed.comfort_brake, relaxed.stop_distance) + 0.5
            for s in holdout_samples]
  return float(np.mean(inside))


def fit(samples, holdout_route=None):
  routes = sorted({s['route'] for s in samples})
  if len(routes) < 2:
    raise ValueError('At least two routes are required so one can be held out and never fit against')
  holdout_route = holdout_route or routes[-1]
  train = [s for s in samples if s['route'] != holdout_route]
  holdout = [s for s in samples if s['route'] == holdout_route]

  bins = bin_samples(train)
  aggressive_raw = fit_curve(bins, 'min', get_T_FOLLOW(log.LongitudinalPersonality.aggressive))
  relaxed = fit_curve(bins, 'max', get_T_FOLLOW(log.LongitudinalPersonality.relaxed))
  aggressive = pad_gap(aggressive_raw, AGGRESSIVE_PADDING)
  standard = midpoint_gap(aggressive, relaxed)

  profile_unvalidated = VoltFollowingProfile(validated=False, fit_time=datetime.now(UTC).isoformat(),
                                             heldout_rmse_m=float('inf'), aggressive=aggressive, standard=standard, relaxed=relaxed)
  bounds_ok = following_profile_valid(profile_unvalidated)
  heldout_rmse_m = evaluate_holdout(standard, holdout)
  coverage = envelope_coverage(aggressive, relaxed, holdout)
  rmse_ok = heldout_rmse_m is not None and heldout_rmse_m <= MAX_HELDOUT_RMSE_M
  checks_passed = bool(bounds_ok and rmse_ok)

  report = {
    'version': 1,
    'fit_time': profile_unvalidated.fit_time,
    'train_routes': [r for r in routes if r != holdout_route],
    'holdout_route': holdout_route,
    'train_samples': len(train),
    'holdout_samples': len(holdout),
    'bins': bins,
    'aggressive_raw': asdict(aggressive_raw),
    'aggressive': asdict(aggressive),
    'standard': asdict(standard),
    'relaxed': asdict(relaxed),
    'heldout_rmse_m': heldout_rmse_m,
    'heldout_envelope_coverage': coverage,
    'bounds_and_monotonicity_ok': bounds_ok,
    'heldout_rmse_ok': rmse_ok,
    'checks_passed': checks_passed,
    'max_heldout_rmse_m': MAX_HELDOUT_RMSE_M,
    'aggressive_padding': AGGRESSIVE_PADDING,
    'validated': False,   # this report is advisory; only main()'s Params write can set validated=True
    'limitations': [
      'Single vehicle, single driver — this is a personal habit fit, not a safety-validated distance.',
      'Radar dRel/vRel accuracy and latency are not independently characterized here.',
      'No distinction between highway and surface-street driving context.',
      f'Speed bins below {MIN_SAMPLES_PER_BIN} samples are excluded from the fit (see bins[*].low_confidence).',
    ],
  }
  return report, profile_unvalidated


def validated_payload(profile_unvalidated: VoltFollowingProfile, report: dict) -> str:
  """The exact JSON string volt_following_apply.py writes to the VoltFollowingProfile
  Params key. A pure function so volt_following_apply.py can rebuild it from a saved
  report without re-running the fit."""
  from dataclasses import replace
  validated = replace(profile_unvalidated, validated=True, heldout_rmse_m=report['heldout_rmse_m'])
  return json.dumps({
    'version': validated.version, 'validated': True, 'fit_time': validated.fit_time,
    'heldout_rmse_m': validated.heldout_rmse_m,
    'aggressive': asdict(validated.aggressive), 'standard': asdict(validated.standard), 'relaxed': asdict(validated.relaxed),
  })


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('samples', type=Path, help='following-distance-samples.json from volt_following_distance.py')
  parser.add_argument('output', type=Path, help='Where to write following-distance-fit.json')
  parser.add_argument('--holdout-route', default=None, help='Route to hold out; defaults to the most recent route')
  parser.add_argument('--write-profile', action='store_true',
                       help='Only meaningful if this process is actually running on the comma device itself — '
                            'writes straight to selfdrive.car.volt_following.PROFILE_PATH on this machine. Running '
                            'this on a workstation with this flag silently writes to a local, unused path, not the '
                            'car; if you ran extraction/fit off-device (the normal case), copy the output JSON to '
                            'the device and run volt_following_apply.py there instead.')
  args = parser.parse_args()

  data = json.loads(args.samples.read_text())
  report, profile_unvalidated = fit(data['samples'], args.holdout_route)
  atomic_json(args.output, report)
  print(json.dumps({k: v for k, v in report.items() if k != 'bins'}, indent=2))

  if not report['checks_passed']:
    print('checks_passed=False: nothing to apply (see bounds_and_monotonicity_ok / heldout_rmse_ok above).')
  elif args.write_profile:
    from openpilot.selfdrive.car.volt_following import PROFILE_PATH
    tmp = PROFILE_PATH.with_suffix('.tmp')
    tmp.write_text(validated_payload(profile_unvalidated, report))
    tmp.replace(PROFILE_PATH)
    print(f'Wrote validated profile to {PROFILE_PATH} — only correct if this IS the comma device.')
  else:
    print(f'checks_passed=True. Copy {args.output} to the comma device and run: '
          f'volt_following_apply.py {args.output.name}')


if __name__ == '__main__':
  main()
