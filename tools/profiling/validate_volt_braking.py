"""Reproduce recorded targets and run independent closed-loop Volt scenarios."""

import argparse
import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import numpy as np
from opendbc.car.gm.volt_longitudinal import PROFILE, profile_valid
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import replay_targets, closed_loop_stop, metrics
from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.tools.profiling.volt_response_fit import fit_style


def matches_manual_finish(result, references):
  """Exploratory design target, not a matched-traffic or road validation claim."""
  if not references:
    return False
  durations = [e['low_speed_seconds'] for e in references]
  return (
    result['stopped']
    and min(durations) - 0.35 <= result['low_speed_seconds'] <= max(durations) + 0.35
    and result['final_jerk_p95'] is not None
    and result['final_jerk_p95'] <= max(e['final_jerk_p95'] for e in references)
    and result['speed_rebound'] <= 0.05
  )


def validate(root):
  fit = json.loads((root / 'response-fit.json').read_text())
  index = json.loads((root / 'index.json').read_text())
  style = fit_style(root)
  atomic_json(root / 'style-fit.json', style)
  taper = style['stopping_candidate']
  modes = [('stock', False, None), ('smooth', True, None)]
  if taper is not None:
    profile = replace(PROFILE, stop_speed=tuple(taper['stop_speed']), stop_decel=tuple(taper['stop_decel']))
    if not profile_valid(profile):
      raise ValueError('Invalid offline manual stopping curve')
    modes.append(('manual_taper', True, profile))
  results = []
  checks = []
  for summary in index['events']:
    if summary['kind'] != 'autonomous':
      continue
    event = json.loads((root / 'events' / (summary['id'] + '.json')).read_text())
    label = datetime.fromisoformat(summary['stop_utc']).astimezone(ZoneInfo('America/Denver')).strftime('%I:%M %p')
    cases = {}
    for mode, smooth, profile in modes:
      trace = replay_targets(event, fit, smooth, profile)
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
    checks.append({
      'name': label + ' candidate finishing pace',
      'pass': matches_manual_finish(cases['smooth'], style['examples']),
      'detail': f"Candidate low-speed phase {cases['smooth']['low_speed_seconds']:.2f} s. "
      + 'Target: the observed manual duration range ±0.35 s, no more jerk than the manual references, and no speed rebound above 0.05 m/s. '
      + 'This is an exploratory design target across different traffic conditions.',
    })
    results.append({
      'event': summary['id'], 'label': label,
      'recorded': {k: summary[k] for k in ('min_accel', 'final_jerk_p95', 'low_speed_seconds')}, 'simulation': cases,
    })
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
    for mode, smooth, profile in modes:
      trace = closed_loop_stop(fit, speed, distance, smooth, grade, regen, delay, stopping_profile=profile)
      pair[mode] = metrics(trace)
    name = f'{speed} m/s, grade {grade}, regen {regen}, extra delay {delay}'
    for mode, _, _ in modes[1:]:
      result = pair[mode]
      good = result['stopped'] and result['minimum_gap'] >= 0.25 and result['speed_rebound'] <= 0.05
      checks.append(
        {
          'name': name + ': ' + mode,
          'pass': good,
          'detail': f"Candidate stopped: {result['stopped']}; minimum simulated gap {result['minimum_gap']:.2f} m; "
          + f"speed rebound {result['speed_rebound']:.2f} m/s.",
        }
      )
    scenarios.append({'name': name, **pair})
    # A gentle but prolonged crawl must not be counted as matching the driver.
    if grade == 0.0 and regen == 1.0 and delay == 0.0:
      for mode, _, _ in modes[1:]:
        checks.append({
          'name': f'{speed} m/s stationary lead: {mode} finishing pace',
          'pass': matches_manual_finish(pair[mode], style['examples']),
          'detail': f"Low-speed phase {pair[mode]['low_speed_seconds']:.2f} s; final jerk {pair[mode]['final_jerk_p95']:.2f} m/s³. "
          + 'Manual duration and jerk are separate targets; a smoother crawl alone is insufficient.',
        })
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
      'detail': 'A manual taper is fitted on the first route and scored on the second, then tested only in the offline plant. '
      + 'It does not change approach planning or terminal gap; no personal profile is enabled.',
    }
  )
  result = {
    'version': 2,
    'summary': 'Stock remains active. The opt-in prototype is locked pending response-model and controlled-vehicle validation.',
    'deployment_ready': False,
    'checks': checks,
    'recorded_cases': results,
    'scenarios': scenarios,
    'manual_reference': {
      'examples': style['examples'],
      'stopping_candidate': taper,
      'duration_tolerance_seconds': 0.35,
      'limitation': 'Different traffic conditions; these are provisional design targets, not matched trials or proof of road behavior.',
    },
  }
  atomic_json(root / 'validation.json', result)
  print(json.dumps({'passed': sum(c['pass'] for c in checks), 'total': len(checks), 'deployment_ready': False}))
  return result


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  validate(p.parse_args().review)
