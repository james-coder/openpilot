"""Recorded reproduction, finite-stop experiments, and version-bound qualification."""

import argparse
from dataclasses import replace
import json
import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import replay_targets, closed_loop_stop, metrics
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_replay import replay_commands, replay_traffic
from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.tools.profiling.volt_response_fit import fit_style, load_routes, prepare
from openpilot.tools.profiling.volt_pressure_model import allocator_profile, evaluate_pressure_response
from openpilot.selfdrive.car.volt_profile import make_bundle, source_hashes, VEHICLE_CHECKS


def matches_manual_finish(result, references):
  if not references or not result['stopped']:
    return False
  durations = [e['low_speed_seconds'] for e in references]
  return (min(durations) - .35 <= result['low_speed_seconds'] <= max(durations) + .35
          and result['final_jerk_p95'] is not None and result['final_jerk_p95'] <= max(e['final_jerk_p95'] for e in references)
          and result['speed_rebound'] <= .05)


def matches_gap(result, curve):
  return result['stopped'] and result['settled_gap'] is not None and abs(result['settled_gap'] - curve['gap']) <= .5


def number(value, unit=''):
  return f'{value:.2f}{unit}' if value is not None and np.isfinite(value) else 'unavailable (stop incomplete)'


def finish_detail(m):
  return (f'Low-speed phase {number(m["low_speed_seconds"], " s")}; final jerk {number(m["final_jerk_p95"], " m/s³")}; '
          + f'rebound {number(m["speed_rebound"], " m/s")}; settled gap {number(m["settled_gap"], " m")}.')


def validate(root, vehicle_evidence=None, kind='brake'):
  if kind not in ('brake', 'personal'):
    raise ValueError('Unknown candidate kind')
  tested_sources = source_hashes()
  fit = json.loads((root / 'response-fit.json').read_text())
  index = json.loads((root / 'index.json').read_text())
  style = fit_style(root)
  atomic_json(root / 'style-fit.json', style)
  approach = style['approach_candidate']
  references = [e for e in style['examples'] if e['route'] != style['split']['holdout_route']]
  modes = [('stock', False, None), ('smooth', True, None)] + ([('personal', True, approach)] if kind == 'personal' and approach else [])
  qualifying_mode = 'smooth' if kind == 'brake' else 'personal'
  checks, recorded, scenarios = [], [], []

  def check(name, passed, detail, category='response', stage='offline'):
    checks.append({'name': name, 'pass': bool(passed), 'detail': detail, 'category': category, 'stage': stage})

  def save(event, name, trace, **metadata):
    atomic_json(root / 'simulation' / (event + '-' + name + '.json'), {'samples': trace, **metadata})
    return '/api/braking/simulation/' + event + '/' + name

  diagnostics = json.loads((root / 'brake-diagnostics.json').read_text()) if (root / 'brake-diagnostics.json').exists() else None
  diagnostics_valid = (diagnostics and diagnostics.get('source_sha256') == tested_sources['tools/profiling/volt_brake_diagnostics.py']
                       and diagnostics.get('model_sha256') == hashlib.sha256(json.dumps(fit['pressure_model'], sort_keys=True).encode()).hexdigest())
  if kind == 'brake':
    check('Pressure diagnostic provenance', bool(diagnostics_valid), 'Cached diagnosis must match the response model and current diagnostic source.')
  if not diagnostics_valid:
    diagnostics = None

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
      ratio = m['final_jerk_p95'] / max(.01, summary['final_jerk_p95']) if m['final_jerk_p95'] is not None else None
      rmse = float(np.sqrt(np.mean([(r['a'] - r['recorded_a']) ** 2 for r in trace])))
      speed_rmse = float(np.sqrt(np.mean([(r['v'] - r['recorded_v']) ** 2 for r in trace])))
      case['reproduction'] = {**m, 'accel_rmse': rmse, 'speed_rmse': speed_rmse,
                              'url': save(summary['id'], 'commands', trace, experiment='recorded_commands')}
      good = m['stopped'] and peak_error <= .5 and ratio is not None and .7 <= ratio <= 1.3 and abs(m['stop_time']) <= .5
      check(label + ' command-response reproduction', good,
            f'Peak error {peak_error:.2f} m/s²; jerk ratio {number(ratio)}; stop timing error {number(m["stop_time"], " s")}. '
            + 'Requires peak error ≤0.5, jerk 70–130%, and stop timing error ≤0.5 s.')
      check(label + ' average response', rmse <= .5 and speed_rmse <= .5,
            f'Acceleration RMSE {rmse:.2f} m/s²; speed RMSE {speed_rmse:.2f} m/s. Both must be ≤0.5.')
    except ValueError as error:
      case['reproduction'] = {'error': str(error)}
      check(label + ' command-response data', False, str(error))

    for mode, smooth, _ in modes[:2]:
      try:
        trace = replay_targets(event, fit, smooth)
        case['simulation'][mode] = {**metrics(trace), 'url': save(summary['id'], 'targets-' + mode, trace, experiment='recorded_targets')}
      except ValueError as error:
        case['simulation'][mode] = {'error': str(error)}

    for mode, smooth, curve in modes:
      stage = 'offline' if mode == qualifying_mode else 'diagnostic'
      try:
        result = replay_traffic(event, fit, smooth, curve)
        trace = result.pop('samples')
        m = metrics(trace)
        case['traffic'][mode] = {**m, **result, 'target_gap': curve['gap'] if curve else None,
          'url': save(summary['id'], 'traffic-' + mode, trace, experiment='reconstructed_traffic', **result)}
        if smooth:
          check(label + ' ' + mode + ' traffic margins', m['minimum_gap'] >= .25 and m['solver_failures'] == 0,
                f'Minimum gap {m["minimum_gap"]:.2f} m; solver failures {m["solver_failures"]}.', 'traffic', stage)
          check(label + ' ' + mode + ' finishing pace', matches_manual_finish(m, references), finish_detail(m), 'traffic', stage)
          if mode == qualifying_mode and kind == 'brake':
            baseline_gap = case['traffic']['stock'].get('minimum_gap')
            check(label + ' brake-only baseline distance margin', baseline_gap is not None and m['minimum_gap'] >= baseline_gap - .1,
                  f'Candidate minimum gap {m["minimum_gap"]:.2f} m; stock-model minimum {baseline_gap}; tolerance 0.1 m.', 'traffic', stage)
          if curve:
            check(label + ' personal final gap', matches_gap(m, curve),
                  finish_detail(m) + f' Target {curve["gap"]:.2f} m ±0.5 m.', 'traffic', stage)
      except ValueError as error:
        case['traffic'][mode] = {'error': str(error)}
        check(label + ' ' + mode + ' traffic data', False, str(error), 'traffic', stage)
    recorded.append(case)
    print(label, 'finished', flush=True)

  # A 2 m/s cruise from 12 m cannot both finish in 2 s and reach a 4.5 m gap.
  # Retain it as a stress case; use an observed >2 m/s entry for the pace test.
  definitions = [('stationary', v, d, 0., 1., 0.) for v, d in [(2.1, 7.5), (5., 25.), (10., 55.), (20., 140.)]]
  definitions += [('slow_approach', 2., 12., 0., 1., 0.)]
  definitions += [('stationary', 5., 25., -.05, 1., .2), ('stationary', 5., 25., .05, .5, .2), ('stationary', 5., 25., 0., 0., .2)]
  definitions += [(name, 5., 25., 0., 1., 0.) for name in
                  ('moving_stop', 'cut_in', 'lead_loss', 'pull_away', 'stop_resume', 'pedal_override', 'full_braking', 'engine_on')]
  for name, speed, distance, grade, regen, delay in definitions:
    title = f'{name}: {speed} m/s, grade {grade}, regen {regen}, extra delay {delay}'
    pair = {'name': title}
    nominal = name == 'stationary' and grade == 0 and regen == 1 and delay == 0
    for mode, smooth, curve in modes:
      stage = 'offline' if mode == qualifying_mode else 'diagnostic'
      category = 'nominal' if nominal else 'stress'
      try:
        trace = closed_loop_stop(fit, speed, distance, smooth, grade, regen, delay, approach_profile=curve, scenario=name)
        m = metrics(trace)
        pair[mode] = m
        if smooth:
          good = m['minimum_gap'] >= .25 and m['solver_failures'] == 0
          if name in ('stationary', 'slow_approach', 'moving_stop', 'stop_resume', 'engine_on'):
            good &= m['stopped']
          if name == 'pedal_override':
            good &= all(not r['active'] and r['cmd'] == 0 for r in trace if 3.01 <= r['t'] < 3.5)
          if name == 'full_braking':
            good &= all(r['cmd'] == -4 for r in trace if 2.01 <= r['t'] < 3 and r['physical_v'] > .0864)
          if name in ('pull_away', 'stop_resume'):
            good &= max((r['physical_v'] for r in trace if 10 <= r['t'] <= 15), default=0.) > 1.
          if name == 'stationary' and grade != 0 and m['stopped']:
            good &= all(r['hold_margin'] is not None and r['hold_margin'] >= 0 for r in trace if r['t'] > trace[-1]['t'] - 1.)
          check(title + ': ' + mode, good,
                f'Minimum gap {m["minimum_gap"]:.2f} m; stopped {m["stopped"]}; solver failures {m["solver_failures"]}.', category, stage)
          if nominal:
            check(title + ': ' + mode + ' finishing pace', matches_manual_finish(m, references), finish_detail(m), category, stage)
            if kind == 'brake':
              check(title + ': brake-only gap', matches_gap(m, {'gap': 6.}), finish_detail(m) + ' Stock planner target 6 m ±0.5 m.', category, stage)
            if curve:
              check(title + ': personal gap', matches_gap(m, curve), finish_detail(m), category, stage)
          if name == 'stationary' and not nominal:
            ceiling = max((e['final_jerk_p95'] for e in references), default=0.)
            check(title + ': ' + mode + ' reduced-regen comfort',
                  m['stopped'] and m['final_jerk_p95'] <= ceiling and m['speed_rebound'] <= .05,
                  finish_detail(m) + f' Manual jerk ceiling {ceiling:.2f}.', category, stage)
      except ValueError as error:
        pair[mode] = {'error': str(error)}
        check(title + ': ' + mode, False, str(error), category, stage)
    scenarios.append(pair)
    print(title, 'finished', flush=True)

  model = fit.get('pressure_model', {})
  low = model.get('train', {}).get('low_speed_rmse')
  check('Low-speed pressure response model', low is not None and low <= .35,
        f'Pooled training acceleration RMSE {low}; required ≤0.35 m/s². Recorded-command reproduction is checked separately.')
  blind_response = None
  reserved = style['split']['holdout_route']
  if reserved:
    rows = load_routes(root / 'cache', reserved).get(reserved, [])
    if rows:
      blind_response = evaluate_pressure_response(prepare(rows), model)
  blind_low = blind_response.get('low_speed_rmse') if blind_response else None
  check('Reserved route low-speed response', blind_low is not None and blind_low <= .35,
        f'Unfitted-route acceleration RMSE {blind_low}; required ≤0.35 m/s² with observed low-speed autonomous response.', 'response', 'release')
  independent = fit.get('independent_pressure_model')
  if independent and (kind == 'brake' or approach):
    alternate = {**fit, 'pressure_model': independent}
    fixed_controller = allocator_profile(model)
    for speed, distance in ((5., 25.), (10., 55.)):
      try:
        m = metrics(closed_loop_stop(alternate, speed, distance, True, approach_profile=approach if kind == 'personal' else None,
                                    controller_profile=fixed_controller))
        good = (matches_manual_finish(m, references) and matches_gap(m, approach if kind == 'personal' else {'gap': 6.})
                and m['minimum_gap'] >= .25 and m['solver_failures'] == 0)
        check(f'Independent response at {speed} m/s', good,
              finish_detail(m) + ' Controller calibration held fixed; plant fitted on a separate route subset.', 'independent_response')
      except ValueError as error:
        check(f'Independent response at {speed} m/s', False, str(error), 'independent_response')
  else:
    check('Independent response model', False, 'A response fit from the other route is required.', 'independent_response')
  check('New manual holdout and speed support', bool(approach and approach['supported_for_release']),
        f'{style["collection"]["qualifying_examples"]} qualifying manual examples; target approximately 10. '
        + 'Requires three per fitted speed band and a reserved evaluation route.',
        'manual', 'release' if kind == 'personal' else 'diagnostic')
  evaluation = approach.get('evaluation_rmse') if approach else None
  check('Reserved manual route agreement', evaluation is not None and evaluation <= .5,
        f'Unfitted manual-route deceleration RMSE {evaluation}; required ≤0.5 m/s².', 'manual', 'release' if kind == 'personal' else 'diagnostic')
  check('Representative manual examples reviewed', bool(style['examples']) and all(e['reviewed'] for e in style['examples']),
        'Optional example review; final driver comfort is checked during the supervised vehicle tests.', 'manual', 'diagnostic')

  bundle = None
  if kind == 'brake' or approach:
    profile = allocator_profile(model)
    if kind == 'personal':
      profile = replace(profile, stop_speed=tuple(approach['speed']), stop_decel=tuple(approach['deceleration']))
    vehicle = json.loads(vehicle_evidence.read_text()) if vehicle_evidence else None
    if source_hashes() != tested_sources:
      raise RuntimeError('Controller or validation sources changed during the run; rerun before exporting qualification')
    bundle = make_bundle(profile, approach if kind == 'personal' else None, checks, vehicle=vehicle, hashes=tested_sources, kind=kind)
    atomic_json(root / 'candidate-bundle.json', bundle)
    # Never overwrite entered physical test evidence.
    template = root / f'vehicle-checks-{bundle["id"][:12]}.json'
    if not template.exists():
      atomic_json(template, {'profile_id': bundle['id'], 'checks': {name: {'pass': None, 'route': None, 'archive_sha256': None} for name in VEHICLE_CHECKS}})
  readiness = bundle['readiness'] if bundle else {'test_ready': False, 'vehicle_validated': False, 'road_ready': False}
  result = {'version': 5, 'profile_kind': kind, 'summary': 'Brake control and personal approach learning are qualified separately.',
            'deployment_ready': readiness['road_ready'], 'readiness': readiness, 'profile_id': bundle['id'] if bundle else None,
            'checks': checks, 'recorded_cases': recorded, 'scenarios': scenarios,
            'manual_reference': {'examples': style['examples'], 'stopping_candidate': style['stopping_candidate'],
                                 'approach_candidate': approach, 'collection': style['collection'], 'split': style['split'],
                                 'duration_tolerance_seconds': .35,
                                 'limitation': 'Different traffic conditions; model comparisons remain provisional until response reproduction passes.'},
            'diagnostics': diagnostics,
            'response_model': model, 'independent_response_model': independent, 'reserved_response': blind_response}
  atomic_json(root / 'validation.json', result)
  print(json.dumps({'passed': sum(c['pass'] for c in checks), 'total': len(checks), **readiness}))
  return result


if __name__ == '__main__':
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('review', type=Path)
  p.add_argument('--vehicle-evidence', type=Path)
  p.add_argument('--kind', choices=('brake', 'personal'), default='brake')
  args = p.parse_args()
  validate(args.review, args.vehicle_evidence, args.kind)
