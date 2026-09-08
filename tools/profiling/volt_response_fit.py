"""Offline identification, with a complete route held out. Never changes runtime profiles."""

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear
from scipy.signal import lfilter

from openpilot.tools.profiling.volt_braking import atomic_json

SPEED = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0])


def basis(v):
  return np.column_stack([np.interp(v, SPEED, np.eye(len(SPEED))[i]) for i in range(len(SPEED))])


def load_routes(cache):
  grouped = {}
  for path in sorted(cache.glob('*.json.gz')):
    data = json.loads(gzip.decompress(path.read_bytes()))
    grouped.setdefault(data['segment'].rsplit('--', 1)[0], []).extend(data['rows'])
  return {key: sorted({r['t']: r for r in rows}.values(), key=lambda r: r['t']) for key, rows in grouped.items()}


def prepare(rows):
  # Uniform grid only for fitting the dynamic plant; original samples stay intact.
  t = np.array([r['t'] for r in rows])
  grid = np.arange(t[0], t[-1], 0.05)

  def channel(key, default=0.0):
    good = [(r['t'], r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    return np.interp(grid, *np.array(good).T) if good else np.full(len(grid), default)

  nearest = np.clip(np.searchsorted(t, grid, side='right') - 1, 0, len(rows) - 1)

  def fresh(key):
    records = [(r['t'], r[key]) for r in rows if isinstance(r.get(key), (int, float, bool))]
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
  v = channel('v')
  a = channel('a')
  mask &= (v > 0.4) & (v < 24) & (np.abs(channel('steering_angle')) < 30)
  return {
    't': grid,
    'v': v,
    'a': a,
    'brake': channel('applied_brake') / 400.0,
    'regen': np.clip(-channel('applied_gas') / 650.0, 0, 1),
    'gas': np.clip(channel('applied_gas') / 1018.0, 0, 1),
    'engine': channel('engine_rpm') > 0,
    'mask': mask,
    'pitch': channel('pitch'),
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


def fit_style(root):
  index = json.loads((root / 'index.json').read_text())
  decisions = json.loads((root / 'decisions.json').read_text()) if (root / 'decisions.json').exists() else {}
  examples = []
  for e in index['events']:
    if not e['recommended_manual'] or decisions.get(e['id']) == 'exclude':
      continue
    event = json.loads((root / 'events' / (e['id'] + '.json')).read_text())
    rows = [r for r in event['samples'] if -8 <= r['t'] <= 0 and not r.get('active') and r['foot']]
    # Human braking only: exclude the first second after leaving active control.
    last_active = max((r['t'] for r in event['samples'] if r.get('active')), default=-100)
    rows = [r for r in rows if r['t'] > last_active + 1]
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
  fit = fit_response(routes)
  atomic_json(args.review / 'response-fit.json', fit)
  atomic_json(args.review / 'style-fit.json', fit_style(args.review))
  print(json.dumps(fit, indent=2))


if __name__ == '__main__':
  main()
