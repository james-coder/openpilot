"""Separate command reproduction, fixed-target diagnostics, and traffic experiments."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import replay_targets, closed_loop_stop, metrics
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_replay import replay_commands, replay_traffic
from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.tools.profiling.volt_response_fit import fit_style


def matches_manual_finish(result, references):
  """Exploratory design target, not a matched-traffic or road validation claim."""
  if not references:
    return False
  durations = [e['low_speed_seconds'] for e in references]
  return (result['stopped'] and min(durations) - .35 <= result['low_speed_seconds'] <= max(durations) + .35
          and result['final_jerk_p95'] is not None and result['final_jerk_p95'] <= max(e['final_jerk_p95'] for e in references)
          and result['speed_rebound'] <= .05)


def validate(root):
  fit = json.loads((root / 'response-fit.json').read_text())
  index = json.loads((root / 'index.json').read_text())
  style = fit_style(root)
  atomic_json(root / 'style-fit.json', style)
  approach = style['approach_candidate']
  modes = [('stock', False, None), ('smooth', True, None)]
  if approach is not None:
    modes.append(('personal', True, approach))
  checks, recorded, scenarios = [], [], []

  def check(name, passed, detail):
    checks.append({'name': name, 'pass': bool(passed), 'detail': detail})

  def save(event, name, trace, **metadata):
    atomic_json(root / 'simulation' / (event + '-' + name + '.json'), {'samples': trace, **metadata})
    return '/api/braking/simulation/' + event + '/' + name

  for summary in index['events']:
    if summary['kind'] != 'autonomous':
      continue
    event = json.loads((root / 'events' / (summary['id'] + '.json')).read_text())
    label = datetime.fromisoformat(summary['stop_utc']).astimezone(ZoneInfo('America/Denver')).strftime('%I:%M:%S %p')
    case = {'event': summary['id'], 'label': label, 'simulation': {}, 'traffic': {},
            'recorded': {k: summary[k] for k in ('min_accel', 'final_jerk_p95', 'low_speed_seconds', 'settled_radar_gap')}}
    try:
      trace = replay_commands(event, fit)
      m = metrics(trace)
      peak_error = abs(m['min_accel'] - summary['min_accel'])
      ratio = m['final_jerk_p95'] / max(.01, summary['final_jerk_p95'])
      rmse = float(np.sqrt(np.mean([(r['a'] - r['recorded_a']) ** 2 for r in trace])))
      speed_rmse = float(np.sqrt(np.mean([(r['v'] - r['recorded_v']) ** 2 for r in trace])))
      case['reproduction'] = {**m, 'accel_rmse': rmse, 'speed_rmse': speed_rmse,
                              'url': save(summary['id'], 'commands', trace, experiment='recorded_commands')}
      check(label + ' command-response reproduction', peak_error <= .5 and .7 <= ratio <= 1.3 and abs(m['stop_time']) <= .5,
            f'Peak acceleration error {peak_error:.2f} m/s²; final jerk {ratio:.0%} of recording; stop timing error {m["stop_time"]:.2f} s. '
            + 'Requires peak error ≤0.5, jerk 70–130%, and stop timing error ≤0.5 s.')
      check(label + ' average response', rmse <= .5 and speed_rmse <= .5,
            f'Acceleration RMSE {rmse:.2f} m/s²; speed RMSE {speed_rmse:.2f} m/s. Both must be ≤0.5; average fit alone is insufficient.')
    except ValueError as error:
      case['reproduction'] = {'error': str(error)}
      check(label + ' command-response data', False, str(error))

    # Preserve the earlier diagnostic, but do not use it as evidence of planner improvement.
    for mode, smooth, _ in modes[:2]:
      try:
        trace = replay_targets(event, fit, smooth)
        case['simulation'][mode] = {**metrics(trace), 'url': save(summary['id'], 'targets-' + mode, trace, experiment='recorded_targets')}
      except ValueError as error:
        case['simulation'][mode] = {'error': str(error)}

    for mode, smooth, curve in modes:
      try:
        result = replay_traffic(event, fit, smooth, curve)
        trace = result.pop('samples')
        m = metrics(trace)
        case['traffic'][mode] = {**m, **result, 'url': save(summary['id'], 'traffic-' + mode, trace, experiment='reconstructed_traffic', **result)}
        if smooth:
          check(label + ' ' + mode + ' traffic margins', m['minimum_gap'] >= .25 and m['solver_failures'] == 0,
                f'Minimum reconstructed gap {m["minimum_gap"]:.2f} m; solver failures {m["solver_failures"]}. '
                + 'This is an exploratory model result, not proof of vehicle behavior.')
          check(label + ' ' + mode + ' finishing pace', matches_manual_finish(m, style['examples']),
                f'Low-speed phase {m["low_speed_seconds"]:.2f} s; final jerk {m["final_jerk_p95"]:.2f} m/s³; '
                + f'speed rebound {m["speed_rebound"]:.2f} m/s. Requires manual duration range ±0.35 s, manual jerk ceiling, rebound ≤0.05 m/s, and a stop.')
      except ValueError as error:
        case['traffic'][mode] = {'error': str(error)}
        check(label + ' ' + mode + ' traffic data', False, str(error))
    recorded.append(case)
    print(label, 'finished', flush=True)

  definitions = [('stationary', v, d, 0., 1., 0.) for v, d in [(2., 12.), (5., 25.), (10., 55.), (20., 140.)]]
  definitions += [('stationary', 5., 25., -.05, 1., .2), ('stationary', 5., 25., .05, .5, .2), ('stationary', 5., 25., 0., 0., .2)]
  definitions += [(name, 5., 25., 0., 1., 0.) for name in
                  ('moving_stop', 'cut_in', 'lead_loss', 'pull_away', 'stop_resume', 'pedal_override', 'full_braking', 'engine_on')]
  for name, speed, distance, grade, regen, delay in definitions:
    title = f'{name}: {speed} m/s, grade {grade}, regen {regen}, extra delay {delay}'
    pair = {'name': title}
    for mode, smooth, curve in modes:
      try:
        trace = closed_loop_stop(fit, speed, distance, smooth, grade, regen, delay, approach_profile=curve, scenario=name)
        m = metrics(trace)
        pair[mode] = m
        if smooth:
          good = m['minimum_gap'] >= .25 and m['solver_failures'] == 0
          if name in ('stationary', 'moving_stop', 'stop_resume', 'engine_on'):
            good &= m['stopped']
          if name == 'pedal_override':
            good &= all(not r['active'] and r['cmd'] == 0 for r in trace if 3.01 <= r['t'] < 3.5)
          if name == 'full_braking':
            good &= all(r['cmd'] == -4 for r in trace if 2.01 <= r['t'] < 3 and r['physical_v'] > .0864)
          check(title + ': ' + mode, good,
                f'Minimum simulated gap {m["minimum_gap"]:.2f} m; stopped {m["stopped"]}; solver failures {m["solver_failures"]}. '
                + 'Engine-on and grade conditions are stress hypotheses, not calibrated vehicle tests.')
          if name == 'stationary' and grade == 0 and regen == 1 and delay == 0:
            check(title + ': ' + mode + ' finishing pace', matches_manual_finish(m, style['examples']),
                  f'Low-speed phase {m["low_speed_seconds"]:.2f} s; final jerk {m["final_jerk_p95"]:.2f} m/s³; rebound {m["speed_rebound"]:.2f} m/s.')
          if name == 'stationary' and (grade != 0 or regen != 1 or delay != 0):
            ceiling = max((e['final_jerk_p95'] for e in style['examples']), default=0.)
            check(title + ': ' + mode + ' comfort under reduced braking',
                  m['final_jerk_p95'] <= ceiling and m['speed_rebound'] <= .05,
                  f'Final jerk {m["final_jerk_p95"]:.2f} m/s³; manual ceiling {ceiling:.2f}. Avoiding contact alone is insufficient for a comfort release.')
      except ValueError as error:
        pair[mode] = {'error': str(error)}
        check(title + ': ' + mode, False, str(error))
    scenarios.append(pair)
    print(title, 'finished', flush=True)

  model = fit.get('pressure_model', {})
  low = model.get('holdout', {}).get('low_speed_rmse')
  check('Low-speed pressure response model', low is not None and low <= .35,
        f'Calibrated vehicle-IMU acceleration RMSE on the second route: {low}. Required ≤0.35 m/s². '
        + 'The September 7 routes have already been inspected; a new blind holdout is still needed.')
  check('New manual holdout and speed support', approach is not None and approach['supported_for_release'],
        f'{style["collection"]["qualifying_examples"]} qualifying manual examples; target approximately 10. '
        + 'Requires at least three training examples per fitted speed band and a newly reserved evaluation route.')
  check('Controlled vehicle validation', False,
        'Not performed. Holding, rollback, grade, engine-on/regen limits, and physical brake response remain unvalidated. No driving profile is released.')
  check('Personal stopping profile', False,
        'The full stopping-distance curve is an offline experiment. Runtime personalization is locked; '
        + 'the observed gap is constrained by existing profile limits.')
  result = {'version': 3, 'summary': 'Stock remains active. The pressure-model and personal-planner experiments are locked pending validation.',
            'deployment_ready': False, 'checks': checks, 'recorded_cases': recorded, 'scenarios': scenarios,
            'manual_reference': {'examples': style['examples'], 'stopping_candidate': style['stopping_candidate'],
                                 'approach_candidate': approach, 'collection': style['collection'], 'split': style['split'],
                                 'duration_tolerance_seconds': .35,
                                 'limitation': 'Different traffic conditions; model comparisons remain provisional until response reproduction passes.'},
            'response_model': model}
  atomic_json(root / 'validation.json', result)
  print(json.dumps({'passed': sum(c['pass'] for c in checks), 'total': len(checks), 'deployment_ready': False}))
  return result


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  validate(p.parse_args().review)
