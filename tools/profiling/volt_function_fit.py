"""Fit two shared function parameters to cached manual stops, never to a plant."""

import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import differential_evolution

from openpilot.selfdrive.controls.lib.volt_polynomial import MODEL, generate
from openpilot.tools.profiling.volt_braking import atomic_json


def manual_windows(event):
  rows = event['samples']
  # The legacy event origin is a 0.3 m/s crossing, not physical standstill.
  ends = [i for i, r in enumerate(rows) if r['t'] >= -.1 and r.get('standstill') and abs(r['vraw']) < .03
          and rows[-1]['t'] >= r['t']+.2 and all(p.get('standstill') and abs(p['vraw']) < .03
          for p in rows[i:] if p['t'] <= r['t']+.2)]
  if not ends:
    return [], 'no confirmed wheel standstill'
  end = ends[0]
  last_active = max((r['t'] for r in rows[:end] if r.get('active')), default=-100.)
  windows = []
  for entry in (2., 5., 10.):
    starts = [i for i, r in enumerate(rows[:end]) if r['v'] >= entry]
    if not starts:
      continue
    segment = rows[starts[-1]:end+1]
    if (any(r.get('active') is True or r.get('gas') or not r.get('valid') for r in segment)
        or any(b['t']-a['t'] > .1 for a, b in zip(segment, segment[1:], strict=False))):
      continue
    times = np.array([r['t'] for r in segment])
    known = np.array([r.get('active') is False and r.get('foot') and r['t'] > last_active+1
                      and abs(r.get('steering_angle', 0)) < 25 for r in segment])
    # Unknown control remains excluded from fitting. Distance uses independently
    # observed wheel motion, not an assumed command in these intervals.
    if known.mean() < .6 or known.sum() < 30:
      continue
    speed = np.array([r['vraw'] for r in segment])
    windows.append({'entry': entry, 'start': float(times[0]), 'stop': float(times[-1]),
                    'v0': float(speed[0]), 'a0': min(0., segment[0]['a']),
                    'distance': float(np.trapezoid(speed, times)), 'duration': float(times[-1]-times[0]),
                    'time': (times[known]-times[0])[::5], 'speed': speed[known][::5],
                    'coverage': float(known.mean())})
  return windows, None if windows else 'no supported uninterrupted manual approach'


def score_window(window, shape):
  r = generate(window['v0'], window['a0'], window['distance'], shape)
  if r is None:
    return None, 1.
  errors = (r.evaluate(window['time'])[:, 1]-window['speed']) / window['v0']
  # Huber loss at 5% of entry speed; no sample must be imitated exactly.
  ae = np.abs(errors)
  loss = np.mean(np.where(ae <= .05, .5*errors**2, .05*(ae-.025)))
  loss += .005 * ((r.duration-window['duration'])/window['duration'])**2
  return r, float(loss)


def fit_functions(root, examples, split):
  root = Path(root)
  paths = [root/'events'/f'{e["id"]}.json' for e in examples]
  from openpilot.selfdrive.controls.lib import volt_polynomial
  provenance = hashlib.sha256(b''.join(p.read_bytes() for p in paths)
                              + json.dumps(split, sort_keys=True).encode()
                              + Path(__file__).read_bytes() + Path(volt_polynomial.__file__).read_bytes()).hexdigest()
  dest = root/'function-fit.json'
  if dest.exists():
    previous = json.loads(dest.read_text())
    if previous.get('provenance') == provenance:
      return previous
  windows, excluded = {}, []
  for e, path in zip(examples, paths, strict=True):
    w, reason = manual_windows(json.loads(path.read_text()))
    windows[e['id']] = w
    if reason:
      excluded.append({'id': e['id'], 'reason': reason})
  training = [e for e in examples if e['route'] != split['holdout_route'] and windows[e['id']]]
  if not training:
    raise ValueError('No manual training windows for function fit')

  def objective(parameters):
    shape = sorted(parameters, reverse=True)
    return float(np.mean([np.mean([score_window(w, shape)[1] for w in windows[e['id']]]) for e in training]))

  result = differential_evolution(objective, [(0., 1.), (0., 1.)], seed=7, maxiter=14, popsize=5, polish=False, tol=1e-5)
  shape = sorted(result.x.tolist(), reverse=True)
  cases = []
  for e in examples:
    fits = []
    for w in windows[e['id']]:
      r, _ = score_window(w, shape)
      entry = {k: v for k, v in w.items() if k not in ('time', 'speed')}
      if r is None:
        fits.append({**entry, 'feasible': False, 'reason': 'no curve within comfort and distance constraints'})
        continue
      error = float(np.sqrt(np.mean((r.evaluate(w['time'])[:, 1]-w['speed'])**2)))
      ts = np.linspace(0., r.duration, 1001)
      values = r.evaluate(ts)
      near = r.evaluate(np.linspace(max(0., r.duration-.1), r.duration, 101))
      def crossing(v, times=ts, points=values):
        return float(times[np.flatnonzero(points[:, 1] <= v)[0]])
      fits.append({**entry, 'feasible': True, 'duration_fit': r.duration, 'speed_rmse': error,
                   'normalized_speed_rmse': error/w['v0'], 'coefficients': r.coefficients.tolist(),
                   'terminal_accel': float(np.max(abs(near[:, 2]))), 'terminal_jerk': float(np.max(abs(near[:, 3]))),
                   'low_speed_seconds': crossing(.3)-crossing(2.), 'creep_seconds': crossing(.03)-crossing(.3),
                   'endpoint': r.evaluate([r.duration])[0].tolist(),
                   'similar': error/w['v0'] <= .1 and abs(r.duration-w['duration']) <= max(1., .2*w['duration']),
                   'samples': [{'t': float(t+w['start']), 'x': float(v[0]), 'v': float(v[1]), 'a': float(v[2]), 'j': float(v[3])}
                               for t, v in zip(ts[::5], values[::5], strict=True)]})
    cases.append({'id': e['id'], 'route': e['route'], 'partition': 'evaluation' if e['route'] == split['holdout_route'] else 'training',
                  'windows': fits})
  support = [{'entry_speed': v, 'events': sum(any(w['entry'] == v for w in windows[e['id']]) for e in training)} for v in (2., 5., 10.)]
  supported = [s['entry_speed'] for s in support if s['events'] >= 3]
  curve = {'version': 2, 'model': MODEL, 'shape': shape, 'max_speed': max(supported, default=0.), 'gap': 4.5,
           'support': support, 'train_ids': [e['id'] for e in training],
           'evaluation_ids': [e['id'] for e in examples if e['route'] == split['holdout_route']],
           'supported_for_release': bool(supported and split['holdout_route']), 'blind_holdout': False,
           'validated': False, 'runtime_applied': False}
  report = {'version': 1, 'provenance': provenance, 'curve': curve, 'cases': cases, 'excluded': excluded,
            'loss': float(result.fun), 'optimizer_converged': bool(result.success),
            'evaluation_note': 'Reserved route excluded from fitting, but previously inspected; not a pristine blind test.',
            'limits': 'Function fit only. Actual brake response qualification is separate. Missing samples are not inferred.'}
  atomic_json(dest, report)
  return report


if __name__ == '__main__':
  import argparse
  from openpilot.tools.profiling.volt_response_fit import fit_style
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  args = p.parse_args()
  report = fit_style(args.review)['function_fit']
  print(json.dumps({k: report[k] for k in ('curve', 'loss', 'optimizer_converged', 'excluded')}, indent=2))
