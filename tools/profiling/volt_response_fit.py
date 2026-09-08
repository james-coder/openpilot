"""Offline identification, with a complete route held out. Never changes runtime profiles."""

import argparse
import gzip
import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
from opendbc.car.gm.volt_longitudinal import PROFILE
from scipy.optimize import lsq_linear
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
  from openpilot.tools.profiling.volt_observations import sample_rows
  grid = np.arange(rows[0]['t'], rows[-1]['t'], .05)
  return prepare_observations(sample_rows(rows, grid))


def prepare_observations(data):
  from openpilot.tools.profiling.volt_observations import autonomous_mask, runs, WARMUP
  grid = data['t']
  continuous = autonomous_mask(data, float(np.median(np.diff(grid))))
  ids = np.full(len(grid), -1, dtype=int)
  warm = np.zeros(len(grid), dtype=bool)
  for number, part in enumerate(runs(continuous)):
    ids[part] = number
    warm[part] = grid[part] - grid[part[0]] >= WARMUP - 1e-6
  return {
    't': grid, 'episode': ids,
    'v': data['v'], 'vraw': data['vraw'], 'a': data['a'],
    'brake': data['applied_brake'] / 400.,
    'regen': np.clip(-data['applied_gas'] / 650., 0, 1),
    'gas': np.clip(data['applied_gas'] / 1018., 0, 1),
    'regen_raw': data['regen_raw'], 'brake_mode': data['brake_mode'],
    'engine': data['engine_rpm'] > 0, 'engine_valid': np.isfinite(data['engine_rpm']),
    'mask': continuous & warm & (data['vraw'] > .1) & (data['vraw'] < 24),
    'pitch': data['controller_pitch'], 'pitch_valid': np.isfinite(data['controller_pitch']),
    'pressure': data['pressure'] / 30000., 'pressure_valid': np.isfinite(data['pressure']),
    'physical_a': data['vehicle_ax'], 'physical_a_valid': np.isfinite(data['vehicle_ax']),
  }


def features(data, delay, tau):
  from openpilot.tools.profiling.volt_pressure_model import episode_lag
  v = data['v']
  regen = episode_lag(data, 'regen', delay, tau)
  brake = episode_lag(data, 'brake', delay, tau)
  return np.column_stack([episode_lag(data, 'gas', delay, tau), -brake, -basis(v) * regen[:, None],
                          np.clip(1 - v / 2., 0, 1), np.ones(len(v))])


def combine_prepared(parts):
  """Pool independent episodes without inventing zero-command interludes."""
  if not parts:
    raise ValueError('No training routes')
  result = {key: np.concatenate([p[key] for p in parts]) for key in parts[0] if key not in ('t', 'episode')}
  ids, offset = [], 0
  for part in parts:
    local = part['episode']
    ids.append(np.where(local >= 0, local + offset, -1))
    offset += int(local.max(initial=-1)) + 1
  result['episode'] = np.concatenate(ids)
  result['t'] = np.arange(len(result['v'])) * .05
  return result


def prepared_routes(root, excluded=()):
  """Prepare one route at a time; cache arrays against source and archive bytes."""
  from openpilot.tools.profiling import volt_observations
  paths = sorted((root / 'cache').glob('*.json.gz'))
  names = sorted({p.name.rsplit('--', 1)[0] for p in paths} - set(excluded))
  result = {}
  directory = root / 'response-cache'
  directory.mkdir(parents=True, exist_ok=True)
  implementation = (inspect.getsource(prepare) + inspect.getsource(prepare_observations)).encode() + Path(volt_observations.__file__).read_bytes()
  for name in names:
    digest = hashlib.sha256(implementation)
    for path in paths:
      if path.name.startswith(name + '--'):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    cached = directory / f'{name}-{digest.hexdigest()[:24]}.npz'
    if cached.exists():
      with np.load(cached, allow_pickle=False) as values:
        data = dict(values)
    else:
      data = prepare(load_routes(root / 'cache', name)[name])
      temporary = cached.with_suffix('.tmp')
      with temporary.open('wb') as stream:
        np.savez_compressed(stream, **data)
      temporary.replace(cached)
    result[name] = data
    print(f'{name}: {int(data["mask"].sum())} qualified fitting samples', flush=True)
  return result


def fit_response(routes):
  names = sorted(routes)
  if len(names) < 2:
    raise ValueError('Two routes are required for an independent holdout')
  train, holdout = routes[names[0]], routes[names[1]]
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
      name: {'on': int((data['engine'] & data['engine_valid']).sum()),
             'off': int((~data['engine'] & data['engine_valid']).sum()), 'unknown': int((~data['engine_valid']).sum())}
      for name, data in routes.items()
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
  from openpilot.tools.profiling.volt_function_fit import fit_functions
  function_fit = fit_functions(root, examples, split)
  gaps = [e['gap'] for e in examples if e['gap'] is not None]
  return {
    'version': 1,
    'examples': examples,
    'speed_bins': [[0, 0.5], [0.5, 1], [1, 2], [2, 5], [5, 10], [10, 20]],
    'median_settled_radar_gap': float(np.median(gaps)) if gaps else None,
    'stopping_candidate': fit_stopping_curve(examples, samples),
    'approach_candidate': function_fit['curve'],
    'function_fit': function_fit,
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
  identification_files = [Path(__file__), Path(__file__).with_name('volt_observations.py'),
                          Path(__file__).with_name('volt_actuator.py'), Path(__file__).with_name('volt_pressure_model.py')]
  sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in identification_files}
  style = fit_style(args.review)  # Reserve a new route before any fitting sees its measurements.
  prepared = prepared_routes(args.review, [style['split']['holdout_route']])
  fit = fit_response(prepared)
  from openpilot.tools.profiling.volt_pressure_model import fit_pressure_response
  names = sorted(prepared)
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
  fit['version'] = 4
  if sources != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in identification_files}:
    raise RuntimeError('Identification sources changed during fitting; rerun before publishing')
  fit['identification_sources'] = sources
  atomic_json(args.review / 'response-fit.json', fit)
  atomic_json(args.review / 'style-fit.json', style)
  print(json.dumps(fit, indent=2))


if __name__ == '__main__':
  main()
