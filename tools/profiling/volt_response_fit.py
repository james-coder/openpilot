"""Offline identification, with a complete route held out. Never changes runtime profiles."""

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np
from opendbc.car.gm.volt_longitudinal import PROFILE
from scipy.optimize import lsq_linear
from scipy.signal import lfilter
from scipy.interpolate import PchipInterpolator

from openpilot.tools.profiling.volt_braking import atomic_json

SPEED = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0])


def basis(v):
  return np.column_stack([np.interp(v, SPEED, np.eye(len(SPEED))[i]) for i in range(len(SPEED))])


def load_routes(cache, route=None):
  grouped = {}
  for path in sorted(cache.glob((route + '--' if route else '') + '*.json.gz')):
    data = json.loads(gzip.decompress(path.read_bytes()))
    grouped.setdefault(data['segment'].rsplit('--', 1)[0], []).extend(data['rows'])
  return {key: sorted({r['t']: r for r in rows}.values(), key=lambda r: r['t']) for key, rows in grouped.items()}


def prepare(rows):
  # Uniform grid only for fitting the dynamic plant; original samples stay intact.
  t = np.array([r['t'] for r in rows])
  grid = np.arange(t[0], t[-1], 0.05)
  services = {'applied_brake': 'carOutput', 'applied_gas': 'carOutput', 'engine_rpm': 'engine',
              'controller_pitch': 'carControl', 'active': 'carControl', 'pressure': 'can_368',
              'vehicle_ax': 'livePose', 'regen_raw': 'can_560'}

  def channel(key, default=0.0, discrete=False):
    service = services.get(key)
    good = sorted({r.get('sample_times', {}).get(service, r['t']): r[key] for r in rows if isinstance(r.get(key), (int, float))}.items())
    if not good:
      return np.full(len(grid), default)
    ts, values = np.array(good).T
    if discrete:
      held = values[np.clip(np.searchsorted(ts, grid, side='right') - 1, 0, len(ts) - 1)]
      return np.where(grid >= ts[0], held, default)
    return np.interp(grid, ts, values)

  nearest = np.clip(np.searchsorted(t, grid, side='right') - 1, 0, len(rows) - 1)

  def fresh(key):
    records = sorted({r.get('sample_times', {}).get(services.get(key), r['t']): r[key]
                      for r in rows if isinstance(r.get(key), (int, float, bool))}.items())
    if not records:
      return np.zeros(len(grid), dtype=bool), np.zeros(len(grid))
    ts, values = np.array(records).T
    pos = np.clip(np.searchsorted(ts, grid, side='right') - 1, 0, len(ts) - 1)
    return (grid - ts[pos] >= 0) & (grid - ts[pos] <= 0.3), values[pos]

  control_fresh, active = fresh('active')
  brake_fresh, _ = fresh('applied_brake')
  gas_fresh, _ = fresh('applied_gas')
  manual = np.array([rows[i]['foot'] or rows[i]['regen'] or rows[i]['gas'] or not rows[i]['valid'] or grid[k] - t[i] > 0.1 for k, i in enumerate(nearest)])
  clear_manual = np.convolve(manual.astype(int), np.ones(41), mode='same') == 0
  mask = control_fresh & active.astype(bool) & brake_fresh & gas_fresh & clear_manual
  mask &= np.array([rows[i].get('active') is True for i in nearest])
  v = channel('v')
  a = channel('a')
  mask &= (v > 0.1) & (v < 24) & (np.abs(channel('steering_angle')) < 30)
  return {
    't': grid,
    'v': v,
    'a': a,
    'brake': channel('applied_brake', discrete=True) / 400.0,
    'regen': np.clip(-channel('applied_gas', discrete=True) / 650.0, 0, 1),
    'gas': np.clip(channel('applied_gas', discrete=True) / 1018.0, 0, 1),
    'engine': channel('engine_rpm', discrete=True) > 0,
    'engine_valid': fresh('engine_rpm')[0],
    'vraw': channel('vraw'),
    'mask': mask,
    'pitch': channel('controller_pitch'),
    'pressure': channel('pressure') / 30000.0,
    'pressure_valid': fresh('pressure')[0],
    'pitch_valid': fresh('controller_pitch')[0],
    'physical_a': channel('vehicle_ax'),
    'physical_a_valid': fresh('vehicle_ax')[0],
  }


def features(data, delay, tau):
  dt = 0.05
  alpha = dt / (tau + dt)

  def lag(x):
    delayed = np.interp(data['t'] - delay, data['t'], x)
    return lfilter([alpha], [1.0, -(1 - alpha)], delayed)

  v = data['v']
  # Engine-on and off are separate coefficients only if observed; no assumption
  # about the factory ASCM enters these command-to-response features.
  regen = lag(data['regen'])
  brake = lag(data['brake'])
  b = basis(v)
  return np.column_stack([lag(data['gas']), -brake, -b * regen[:, None], np.clip(1 - v / 2.0, 0, 1), np.ones(len(v))])


def combine_prepared(parts):
  """Pool fitting grids with an invalid four-second reset between drives."""
  if not parts:
    raise ValueError('No training routes')
  result = {}
  for key in parts[0]:
    if key == 't':
      continue
    values = []
    for part in parts:
      values.extend((np.zeros(80, dtype=part[key].dtype), part[key]))
    result[key] = np.concatenate(values)
  result['t'] = np.arange(len(result['v'])) * .05
  return result


def fit_response(routes):
  names = sorted(routes)
  if len(names) < 2:
    raise ValueError('Two routes are required for an independent holdout')
  train, holdout = prepare(routes[names[0]]), prepare(routes[names[1]])
  bounds = ([0.5, 0.5] + [0.0] * len(SPEED) + [0.0, -0.5], [4.0, 8.0] + [2.0] * len(SPEED) + [0.5, 0.3])
  candidates = []
  for delay in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
    for tau in (0.1, 0.2, 0.35, 0.5):
      X = features(train, delay, tau)
      mask = train['mask']
      # Keep fit cost independent of route duration and robust to isolated shocks.
      weights = np.ones(mask.sum())
      for _ in range(3):
        coef = lsq_linear(X[mask] * weights[:, None], train['a'][mask] * weights, bounds=bounds).x
        residual = X[mask] @ coef - train['a'][mask]
        weights = np.sqrt(np.minimum(1.0, 0.35 / np.maximum(np.abs(residual), 1e-6)))
      score = float(np.sqrt(np.mean(residual**2)))
      candidates.append((score, delay, tau, coef))
  score, delay, tau, coef = min(candidates, key=lambda c: c[0])

  def evaluate(data):
    residual = features(data, delay, tau) @ coef - data['a']
    m = data['mask']
    low = m & (data['v'] < 2)
    return {
      'samples': int(m.sum()),
      'rmse': float(np.sqrt(np.mean(residual[m] ** 2))),
      'p95_absolute_error': float(np.percentile(np.abs(residual[m]), 95)),
      'low_speed_samples': int(low.sum()),
      'low_speed_rmse': float(np.sqrt(np.mean(residual[low] ** 2))) if low.any() else None,
    }

  train_score, test_score = evaluate(train), evaluate(holdout)
  support = [int((train['mask'] & (abs(train['v'] - v) < max(0.3, v * 0.15))).sum()) for v in SPEED]
  return {
    'version': 1,
    'train_route': names[0],
    'holdout_route': names[1],
    'delay': delay,
    'tau': tau,
    'speed': SPEED.tolist(),
    'coefficients': coef.tolist(),
    'speed_support': support,
    'train': train_score,
    'holdout': test_score,
    'engine_samples': {
      name: dict(Counter('on' if r.get('engine_rpm', 0) > 0 else 'off_or_unknown' for r in rows if r.get('active'))) for name, rows in routes.items()
    },
    'validated': False,
    'limitations': [
      'This low-order fitted model is an identification baseline, not proof of vehicle behavior.',
      'Battery state/regen limits and grade effects are not independently identified.',
      'Sparse low-speed samples cannot identify each speed knot reliably.',
    ],
  }


def fit_stopping_curve(examples, samples):
  """A host-only taper hypothesis; fit one route and score the other unchanged.

  Equal speed bins avoid overweighting time spent creeping. The unsupported
  standstill endpoint retains the existing candidate value. No runtime profile
  or review decision is changed by this fit.
  """
  routes = sorted({e['route'] for e in examples})
  if len(routes) < 2:
    return None
  train_ids = [e['id'] for e in examples if e['route'] == routes[0]]
  holdout_ids = [e['id'] for e in examples if e['route'] != routes[0]]
  train = [r for key in train_ids for r in samples[key] if 0.3 <= r['v'] < 2 and r['a'] < -0.05]
  knots, decel, support = [0.0], [PROFILE.stop_decel[0]], []
  for lo, hi in ((0.3, 0.5), (0.5, 0.75), (0.75, 1), (1, 1.5), (1.5, 2)):
    rows = [r for r in train if lo <= r['v'] < hi]
    support.append({'speed_bin': [lo, hi], 'samples': len(rows)})
    if len(rows) >= 10:
      knots.append(float(np.median([r['v'] for r in rows])))
      decel.append(float(np.clip(np.median([-r['a'] for r in rows]), decel[-1], 1.2)))
  if len(knots) < 3:
    return None
  holdout = [r for key in holdout_ids for r in samples[key] if knots[1] <= r['v'] <= knots[-1] and r['a'] < -0.05]
  errors = [-r['a'] - np.interp(r['v'], knots, decel) for r in holdout]
  return {
    'stop_speed': knots,
    'stop_decel': decel,
    'train_ids': train_ids,
    'holdout_ids': holdout_ids,
    'support': support,
    'holdout_samples': len(errors),
    'holdout_rmse': float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
    'validated': False,
    'runtime_applied': False,
    'limitations': [
      'Only the moving-stop taper is fitted; approach planning and terminal gap remain unchanged.',
      'The zero-speed endpoint is the existing candidate assumption, not a measured manual braking target.',
      'Two manual finishes in different traffic do not establish a general personal stopping policy.',
    ],
  }


def study_split(root, index):
  path = root / 'study-split.json'
  routes = sorted({e['route'] for e in index['events']})
  split = json.loads(path.read_text()) if path.exists() else {'version': 1, 'regression_routes': routes, 'holdout_route': None}
  if split['holdout_route'] is None:
    counts = {r: sum(e['recommended_manual'] and e['route'] == r for e in index['events']) for r in routes}
    eligible = sorted((r for r in routes if r not in split['regression_routes'] and counts[r]), key=lambda r: (-counts[r], r))
    if eligible:
      split['holdout_route'] = eligible[0]
  atomic_json(path, split)
  return split


def approach_curve(examples, samples, split):
  training = [e for e in examples if e['route'] != split['holdout_route']]
  # Both old trips have already been inspected. Use their manual examples for
  # the candidate; only a newly reserved route is a blind evaluation.
  speed, decel, support = [0.0], [0.18], []
  for lo, hi in ((0.3, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 20)):
    per_event, velocities = [], []
    for e in training:
      rows = [r for r in samples[e['id']] if lo <= r['v'] < hi and -3.0 <= r['a'] < -0.05]
      if len(rows) >= 10:
        per_event.append(float(np.median([-r['a'] for r in rows])))
        velocities.append(float(np.median([r['v'] for r in rows])))
    support.append({'speed_bin': [lo, hi], 'events': len(per_event)})
    if not per_event:
      break  # Never extrapolate through an unsupported speed range.
    speed.append(float(np.median(velocities)))
    decel.append(float(np.clip(np.median(per_event), 0.18, 2.5)))
  if len(speed) < 3:
    return None
  grid = np.linspace(0, speed[-1], 1001)
  b = np.interp(grid, speed, decel)
  integrand = grid / b
  distance = np.r_[0, np.cumsum((integrand[1:] + integrand[:-1]) * np.diff(grid) / 2)]
  # Fixed six intervals keep the generated MPC interface independent of sample count.
  knots = np.linspace(0, speed[-1], 7)
  values = np.interp(knots, grid, distance)
  spline = PchipInterpolator(knots, values)
  gaps = [e['gap'] for e in training if e['gap'] is not None]
  observed_gap = float(np.median(gaps)) if gaps else None
  gap = float(np.clip(observed_gap if observed_gap is not None else PROFILE.stop_distance, 4.5, 8))
  test = [e for e in examples if e not in training]
  residuals = [-r['a'] - np.interp(r['v'], speed, decel) for e in test for r in samples[e['id']]
               if 0.3 <= r['v'] <= speed[-1] and r['a'] < -0.05]
  return {
    'version': 1, 'speed': speed, 'deceleration': decel,
    'knots': knots.tolist(), 'distance': values.tolist(), 'coefficients': spline.c.T.tolist(),
    'gap': gap, 'observed_gap': observed_gap, 'support': support,
    'train_ids': [e['id'] for e in training], 'evaluation_ids': [e['id'] for e in test],
    'evaluation_rmse': float(np.sqrt(np.mean(np.square(residuals)))) if residuals else None,
    'blind_holdout': split['holdout_route'] is not None,
    'supported_for_release': all(s['events'] >= 3 for s in support) and split['holdout_route'] is not None,
    'validated': False, 'runtime_applied': False,
  }


def fit_style(root):
  index = json.loads((root / 'index.json').read_text())
  decisions = json.loads((root / 'decisions.json').read_text()) if (root / 'decisions.json').exists() else {}
  split = study_split(root, index)
  examples, samples = [], {}
  for e in index['events']:
    if not e['recommended_manual'] or decisions.get(e['id']) == 'exclude':
      continue
    event = json.loads((root / 'events' / (e['id'] + '.json')).read_text())
    rows = [r for r in event['samples'] if -35 <= r['t'] <= 0 and r.get('active') is False and r.get('valid') and r['foot']
            and abs(r.get('steering_angle', 0)) < 25]
    # Human braking only: exclude the first second after leaving active control.
    last_active = max((r['t'] for r in event['samples'] if r.get('active')), default=-100)
    rows = [r for r in rows if r['t'] > last_active + 1]
    samples[e['id']] = rows
    bins = []
    for lo, hi in ((0, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 10), (10, 20)):
      values = [-r['a'] for r in rows if lo <= r['v'] < hi and r['a'] < -0.05]
      bins.append(float(np.median(values)) if len(values) >= 10 else None)
    examples.append(
      {
        'id': e['id'],
        'route': e['route'],
        'reviewed': decisions.get(e['id']) == 'representative',
        'gap': e['settled_radar_gap'],
        'deceleration_by_speed': bins,
        'low_speed_seconds': e['low_speed_seconds'],
        'final_jerk_p95': e['final_jerk_p95'],
      }
    )
  gaps = [e['gap'] for e in examples if e['gap'] is not None]
  return {
    'version': 1,
    'examples': examples,
    'speed_bins': [[0, 0.5], [0.5, 1], [1, 2], [2, 5], [5, 10], [10, 20]],
    'median_settled_radar_gap': float(np.median(gaps)) if gaps else None,
    'stopping_candidate': fit_stopping_curve(examples, samples),
    'approach_candidate': approach_curve(examples, samples, split),
    'split': split,
    'collection': {'target_examples': 10, 'qualifying_examples': len(examples),
                   'review_ids': [e['id'] for e in examples if not e['reviewed'] and e['route'] != split['holdout_route']][:5]},
    'validated': False,
    'runtime_applied': False,
    'reason': (
      'Personal profile requires reviewed examples, route holdout validation, and a validated actuator model. Raw radar gap is not a planner STOP_DISTANCE.'
    ),
  }


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('review', type=Path)
  args = parser.parse_args()
  routes = load_routes(args.review / 'cache')
  style = fit_style(args.review)  # Reserve a new route before any fitting sees its measurements.
  routes = {k: v for k, v in routes.items() if k != style['split']['holdout_route']}
  fit = fit_response(routes)
  from openpilot.tools.profiling.volt_pressure_model import fit_pressure_response
  names = sorted(routes)
  prepared = {name: prepare(routes[name]) for name in names}
  pressure = fit_pressure_response(combine_prepared(list(prepared.values())), prepared[names[-1]], 'pooled-training', names[-1])
  pressure['train_routes'] = names
  pressure['holdout_kind'] = 'inspected regression; not independent of calibration'
  fit['pressure_model'] = pressure
  alternate_names = names[1::2]
  alternate = fit_pressure_response(combine_prepared([prepared[name] for name in alternate_names]), prepared[names[0]],
                                   'route-subset', names[0])
  alternate['train_routes'] = alternate_names
  alternate['holdout_kind'] = 'unfitted route; previously inspected'
  fit['independent_pressure_model'] = alternate
  fit['version'] = 3
  atomic_json(args.review / 'response-fit.json', fit)
  atomic_json(args.review / 'style-fit.json', style)
  print(json.dumps(fit, indent=2))


if __name__ == '__main__':
  main()
