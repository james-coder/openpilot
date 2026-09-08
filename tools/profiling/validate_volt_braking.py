"""Reproduce recorded targets and run independent closed-loop Volt scenarios."""

import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import replay_targets, closed_loop_stop, metrics
from openpilot.tools.profiling.volt_braking import atomic_json


def validate(root):
  fit = json.loads((root / 'response-fit.json').read_text())
  index = json.loads((root / 'index.json').read_text())
  results = []
  checks = []
  for summary in index['events']:
    if summary['kind'] != 'autonomous':
      continue
    event = json.loads((root / 'events' / (summary['id'] + '.json')).read_text())
    label = datetime.fromisoformat(summary['stop_utc']).astimezone(ZoneInfo('America/Denver')).strftime('%I:%M %p')
    cases = {}
    for smooth in (False, True):
      trace = replay_targets(event, fit, smooth)
      mode = 'smooth' if smooth else 'stock'
      cases[mode] = metrics(trace)
      if not smooth:
        rmse = float(np.sqrt(np.mean([(r['a'] - r['recorded_a']) ** 2 for r in trace])))
        checks.append(
          {
            'name': label + ' acceleration tracking',
            'pass': rmse <= 0.5,
            'detail': f'Recorded-target acceleration RMSE {rmse:.3f} m/s² (gate ≤0.5). This is not a counterfactual traffic replay.',
          }
        )
      atomic_json(root / 'simulation' / (summary['id'] + '-' + mode + '.json'), {'mode': mode, 'samples': trace})
    peak_error = abs(cases['stock']['min_accel'] - summary['min_accel'])
    jerk_ratio = cases['stock']['final_jerk_p95'] / max(0.01, summary['final_jerk_p95'])
    checks.append(
      {
        'name': label + ' peak and final jolt reproduction',
        'pass': peak_error <= 0.5 and 0.7 <= jerk_ratio <= 1.3,
        'detail': f'Peak acceleration error {peak_error:.2f} m/s²; final jerk is {jerk_ratio:.0%} of the recording. '
        + 'A good average fit does not establish that the jolt is reproduced.',
      }
    )
    improved = cases['smooth']['stopped'] and cases['smooth']['final_jerk_p95'] < cases['stock']['final_jerk_p95']
    checks.append(
      {
        'name': label + ' candidate stop in model',
        'pass': improved,
        'detail': f"Final jerk: stock model {cases['stock']['final_jerk_p95']:.2f}, candidate {cases['smooth']['final_jerk_p95']:.2f} m/s³. "
        + 'This comparison remains provisional until the reproduction checks pass.',
      }
    )
    results.append({'event': summary['id'], 'recorded': {k: summary[k] for k in ('min_accel', 'final_jerk_p95')}, 'simulation': cases})
  scenarios = []
  for speed, distance, grade, regen, delay in [
    (2.0, 12.0, 0.0, 1.0, 0.0),
    (5.0, 25.0, 0.0, 1.0, 0.0),
    (10.0, 55.0, 0.0, 1.0, 0.0),
    (20.0, 140.0, 0.0, 1.0, 0.0),
    (5.0, 25.0, -0.05, 1.0, 0.2),
    (5.0, 25.0, 0.05, 0.5, 0.2),
    (5.0, 25.0, 0.0, 0.0, 0.2),
  ]:
    pair = {}
    for smooth in (False, True):
      trace = closed_loop_stop(fit, speed, distance, smooth, grade, regen, delay)
      pair['smooth' if smooth else 'stock'] = metrics(trace)
    name = f'{speed} m/s, grade {grade}, regen {regen}, extra delay {delay}'
    good = pair['smooth']['stopped'] and pair['smooth']['minimum_gap'] >= 0.25 and pair['smooth']['speed_rebound'] <= 0.05
    checks.append(
      {
        'name': name,
        'pass': good,
        'detail': f"Candidate stopped: {pair['smooth']['stopped']}; minimum simulated gap {pair['smooth']['minimum_gap']:.2f} m; "
        + f"speed rebound {pair['smooth']['speed_rebound']:.2f} m/s.",
      }
    )
    scenarios.append({'name': name, **pair})
  low = fit['holdout']['low_speed_rmse']
  checks.append(
    {'name': 'Independent low-speed response model', 'pass': low is not None and low <= 0.35, 'detail': f'Holdout low-speed RMSE: {low}. Required ≤0.35 m/s².'}
  )
  checks.append(
    {
      'name': 'Controlled vehicle validation',
      'pass': False,
      'detail': 'Not performed. Stationary holding, engine-on, regeneration limits, and physical stopping response remain unvalidated.',
    }
  )
  checks.append(
    {
      'name': 'Personal stopping profile',
      'pass': False,
      'detail': 'Manual reference fits are prepared for review; no personalized gap or braking curve is enabled.',
    }
  )
  result = {
    'version': 1,
    'summary': 'Stock remains active. The opt-in prototype is locked pending response-model and controlled-vehicle validation.',
    'deployment_ready': False,
    'checks': checks,
    'recorded_cases': results,
    'scenarios': scenarios,
  }
  atomic_json(root / 'validation.json', result)
  print(json.dumps({'passed': sum(c['pass'] for c in checks), 'total': len(checks), 'deployment_ready': False}))
  return result


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  validate(p.parse_args().review)
