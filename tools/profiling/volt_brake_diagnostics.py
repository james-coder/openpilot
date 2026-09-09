"""Cached event-level pressure and motion diagnosis. No device or CAN access."""

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import lsq_linear
from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.tools.profiling.volt_pressure_model import PressureDynamics, response_features
from openpilot.tools.profiling.volt_response_fit import prepare_observations
from openpilot.tools.profiling.volt_observations import sample_rows, autonomous_mask
from openpilot.selfdrive.car.volt_profile import source_hashes

DT = .05
FIELDS = {
  'vraw': None, 'v': None, 'a': None, 'foot': None, 'pedal': None, 'gas': None, 'regen': None, 'valid': None,
  'steering_angle': None, 'active': 'carControl', 'applied_brake': 'carOutput', 'applied_gas': 'carOutput',
  'pressure': 'can_368', 'brake_mode': 'can_789', 'regen_raw': 'can_560', 'engine_rpm': 'engine',
  'controller_pitch': 'carControl', 'vehicle_ax': 'livePose',
}


def event_arrays(event):
  rows = event['samples']
  origin = event['stop_mono']
  ticks = np.arange(np.ceil((rows[0]['t'] + origin) / DT), np.floor((rows[-1]['t'] + origin) / DT) + 1, dtype=np.int64)
  t = ticks * DT - origin
  return {**sample_rows(rows, t), 'ticks': ticks}


def cached_event(path, cache):
  raw = path.read_bytes()
  event = json.loads(raw)
  identity = hashlib.sha256(Path(__file__).read_bytes() + raw).hexdigest()
  target = cache / (event['id'] + '-' + identity[:20] + '.npz')
  if target.exists():
    with np.load(target, allow_pickle=False) as saved:
      return event, {k: saved[k] for k in saved.files}
  data = event_arrays(event)
  cache.mkdir(parents=True, exist_ok=True)
  tmp = target.with_suffix('.tmp')
  with tmp.open('wb') as stream:
    np.savez_compressed(stream, **data)
  tmp.replace(target)
  return event, data


def runs(mask, minimum=2):
  indices = np.flatnonzero(mask)
  return [part for part in np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1) if len(part) >= minimum]


def masks(data):
  valid = ((data['valid'] == 1) & np.isfinite(data['vraw']) & np.isfinite(data['pressure'])
           & np.isfinite(data['controller_pitch']) & (np.abs(data['steering_angle']) < 25))
  autonomous = autonomous_mask(data, DT)
  moving = (data['vraw'] > .1) & (data['vraw'] < 2)
  # Measured pressure can identify physical response during manual braking;
  # it must never be paired with openpilot's inactive brake command as training input.
  physical = valid & moving & (data['engine_rpm'] == 0) & (data['regen_raw'] == 0) & (data['gas'] == 0) & np.isfinite(data['vehicle_ax'])
  return autonomous, physical & (autonomous | ((data['active'] == 0) & (data['foot'] == 1)))


def pressure_prediction(data, model, onset_delay=0., onset_rise=None):
  """Optional pressure-onset experiment; modulation retains the fitted kernel."""
  mask, _ = masks(data)
  output = np.full(len(mask), np.nan)
  for part in runs(mask):
    eligible = part[data['t'][part] >= data['t'][part[0]] + model['pressure']['delay'] + DT - 1e-8]
    if not len(eligible):
      continue
    first = eligible[0]
    dynamics = PressureDynamics(model, dt=DT)
    history = [(data['t'][i], data['applied_brake'][i] / 400.) for i in part if i <= first]
    dynamics.pressure_lag.seed(data['t'][first], history, data['pressure'][first] / 30000.)
    dynamics.gas_lag.time = dynamics.regen_lag.time = data['t'][first]
    output[first] = data['pressure'][first]
    previous_command = data['applied_brake'][first]
    onset = False
    wait = 0
    for i, after in zip(eligible[:-1], eligible[1:], strict=True):
      command = data['applied_brake'][i]
      if command > 0 and previous_command <= 0 and dynamics.pressure < .01:
        onset, wait = True, round(onset_delay / DT)
      previous_command = command
      old_pressure = dynamics.pressure
      dynamics.step(data['applied_gas'][i], command, data['vraw'][i], data['controller_pitch'][i])
      if onset and dynamics.pressure > old_pressure:
        if wait > 0:
          dynamics.pressure = old_pressure
          wait -= 1
        elif onset_rise is not None:
          dynamics.pressure = old_pressure + (dynamics.pressure - old_pressure) * (-np.expm1(-DT/onset_rise)) / (-np.expm1(-DT/model['pressure']['rise']))
      if command <= 0 or dynamics.pressure >= .05:
        onset = False
      output[after] = dynamics.pressure * 30000
  return output


def rmse(values):
  return float(np.sqrt(np.mean(np.square(values)))) if len(values) else None


def motion_error(data, model, predicted_pressure):
  """Measurement-conditioned force diagnosis, never free-running qualification."""
  prepared = prepare_observations(data)
  X = response_features(prepared, model['regen_delay'], *model['regen_fade_speed'], coupled=len(model['coefficients']) > 5)
  measured = X @ model['coefficients'] - 9.81 * np.sin(data['controller_pitch'])
  calculated = measured + model['coefficients'][1] * (data['pressure'] - predicted_pressure) / 30000.
  mask = prepared['mask'] & (data['engine_rpm'] == 0) & np.isfinite(measured) & np.isfinite(calculated)
  stages = []
  for part in runs(mask, minimum=20):
    velocity = data['vraw'][part]
    def error(accel, velocity=velocity, part=part):
      predicted = velocity[0] + np.r_[0., np.cumsum((accel[part][1:] + accel[part][:-1]) * DT / 2)]
      return {'speed_rmse': rmse(predicted - velocity), 'end_speed_error': float(predicted[-1] - velocity[-1])}
    stages.append({'start': float(data['t'][part[0]]), 'end': float(data['t'][part[-1]]),
                   'command_pressure_to_motion': error(calculated), 'measured_pressure_to_motion': error(measured),
                   'conditioned_on': 'observed speed, pitch and initial velocity'})
  return stages


def physical_fit(items, source):
  design, target, episodes = [], [], []
  for _event, data in items:
    autonomous, mask = masks(data)
    mask &= autonomous if source == 'autonomous' else (data['active'] == 0) & (data['foot'] == 1)
    X = np.column_stack([-data['pressure'] / 30000, np.maximum(0, 1 - data['vraw'] / 2), np.ones(len(mask))])
    y = data['vehicle_ax'] + 9.81 * np.sin(data['controller_pitch'])
    design.extend(X[mask])
    target.extend(y[mask])
    episodes.extend((data, part, X) for part in runs(mask, minimum=10))
  if len(target) < 20:
    return {'samples': len(target), 'coefficients': None, 'reason': 'Insufficient fresh zero-regen moving observations'}
  X, y = np.asarray(design), np.asarray(target)
  fit = lsq_linear(X, y, bounds=([.2, 0., -.3], [8., .6, .3]))
  errors = []
  for data, part, features in episodes:
    accel = features[part] @ fit.x - 9.81 * np.sin(data['controller_pitch'][part])
    predicted_change = np.sum((accel[1:] + accel[:-1]) * DT / 2)
    errors.append(float(predicted_change - (data['vraw'][part[-1]] - data['vraw'][part[0]])))
  return {'samples': len(y), 'seconds': len(y) * DT, 'coefficients': fit.x.tolist(), 'acceleration_rmse': rmse(X @ fit.x - y),
          'episode_count': len(errors), 'episode_velocity_change_rmse': rmse(errors), 'converged': bool(fit.success)}


def regen_command_audit(items, evaluation_route):
  """Test a joint-command association without assigning units to raw regen.

  Fit on other routes and evaluate this route unchanged. Conditioning on speed
  and negative-gas demand distinguishes a brake-command association from the
  already modeled speed fade; it does not establish causal EBCM semantics.
  """
  partitions = {'training': [], 'evaluation': []}
  associations = []
  for event, data in items:
    mask = masks(data)[0] & np.isfinite(data['regen_raw']) & (data['vraw'] > .3) & (data['vraw'] < 10)
    if mask.sum() < 20:
      continue
    speed = data['vraw'][mask]
    X = np.column_stack([np.ones(len(speed)), speed / 10., (speed / 10.)**2, data['applied_gas'][mask] / 650.])
    brake, y = data['applied_brake'][mask] / 400., data['regen_raw'][mask]
    residual_y = y - X @ np.linalg.lstsq(X, y, rcond=None)[0]
    residual_brake = brake - X @ np.linalg.lstsq(X, brake, rcond=None)[0]
    correlation = float(np.corrcoef(residual_y, residual_brake)[0, 1]) if min(np.std(residual_y), np.std(residual_brake)) > 1e-8 else None
    associations.append({'id': event['id'], 'partial_correlation': correlation, 'samples': len(y),
                         'regen_with_brake_and_zero_pressure_samples': int((mask & (data['pressure'] < 300)
                                                                                 & (data['regen_raw'] > 0) & (data['applied_brake'] > 0)).sum())})
    partitions['evaluation' if event['route'] == evaluation_route else 'training'].append((X, brake, y))
  result = {'events': associations, 'evaluation_route': evaluation_route, 'runtime_applied': False,
            'interpretation': 'Raw-signal association, not torque calibration or proof of EBCM allocation semantics.'}
  if all(partitions.values()):
    train, test = (tuple(np.concatenate([p[i] for p in partitions[name]]) for i in range(3)) for name in ('training', 'evaluation'))
    for name, joint in (('gas_and_speed', False), ('joint_brake_gas_and_speed', True)):
      X, b, y = train
      design = np.column_stack([X, b]) if joint else X
      coefficient = np.linalg.lstsq(design, y, rcond=None)[0]
      X, b, y = test
      evaluated = np.column_stack([X, b]) if joint else X
      result[name] = {'coefficients': coefficient.tolist(), 'evaluation_rmse_raw': rmse(evaluated @ coefficient - y), 'samples': len(y)}
  return result


def diagnose(root):
  model = json.loads((root / 'response-fit.json').read_text())['pressure_model']
  split = json.loads((root / 'study-split.json').read_text())
  items = []
  for path in sorted((root / 'events').glob('*.json')):
    event, data = cached_event(path, root / 'diagnostic-cache')
    items.append((event, data))
  # Keep full windows for diagnosis. Deduplicate only fitting/support data,
  # prioritizing complete autonomous events when retained windows overlap.
  training, seen = [], set()
  for event, data in sorted(items, key=lambda item: (item[0]['kind'] != 'autonomous', item[0]['id'])):
    if event['route'] == split['holdout_route']:
      continue
    unique = {**data, 'valid': data['valid'].copy()}
    duplicate = np.array([(event['route'], int(t)) in seen for t in data['ticks']])
    seen.update((event['route'], int(t)) for t in data['ticks'])
    unique['valid'][duplicate] = 0
    training.append((event, unique))
  predictions = {e['id']: pressure_prediction(d, model) for e, d in items}
  events = []
  for e, d in items:
    auto, physical = masks(d)
    low = auto & (d['vraw'] > .1) & (d['vraw'] < 2)
    valid = auto & np.isfinite(predictions[e['id']])
    events.append({'id': e['id'], 'kind': e['kind'], 'reserved': e['route'] == split['holdout_route'],
                   'autonomous_low_seconds': int(low.sum()) * DT, 'physical_low_seconds': int(physical.sum()) * DT,
                   'pressure_onsets': int(np.sum(auto[1:] & auto[:-1] & (d['applied_brake'][1:] > 0)
                                                & (d['applied_brake'][:-1] <= 0) & (d['pressure'][:-1] < 300))),
                   'command_pressure_rmse_raw': rmse((predictions[e['id']] - d['pressure'])[valid]),
                   'low_pressure_rmse_raw': rmse((predictions[e['id']] - d['pressure'])[valid & low]),
                   'motion_episodes': motion_error(d, model, predictions[e['id']]),
                   'brake_modes': sorted(np.unique(d['brake_mode'][auto]).astype(int).tolist())})
  # Select on training routes only. A leave-route-out score is reported separately;
  # it is diagnostic and never silently substitutes a new runtime calibration.
  evaluation_route = split['regression_routes'][-1]
  onset_train = [(e, d) for e, d in training if e['route'] != evaluation_route]
  onset_test = [(e, d) for e, d in training if e['route'] == evaluation_route]
  def score(subset, delay, rise):
    errors = []
    for _, d in subset:
      predicted = pressure_prediction(d, model, delay, rise)
      auto, _ = masks(d)
      mask = auto & np.isfinite(predicted) & (d['vraw'] < 3.)
      errors.extend((predicted - d['pressure'])[mask])
    return rmse(errors)
  candidates = []
  for delay in (0., .1, .2, .4):
    for rise in (model['pressure']['rise'], .1, .2, .4):
      error = score(onset_train, delay, rise)
      if error is not None:
        candidates.append((error, delay, rise))
  best = min(candidates) if candidates else None
  onset = None if best is None else {'train_rmse_raw': best[0], 'onset_delay': best[1], 'onset_rise': best[2],
          'evaluation_route': evaluation_route, 'evaluation_rmse_raw': score(onset_test, best[1], best[2]),
          'baseline_evaluation_rmse_raw': score(onset_test, 0., model['pressure']['rise']), 'runtime_applied': False}
  if onset is not None:
    onset['training_onsets'] = sum(int(np.sum(masks(d)[0][1:] & masks(d)[0][:-1] & (d['applied_brake'][1:] > 0)
                                             & (d['applied_brake'][:-1] <= 0) & (d['pressure'][:-1] < 300))) for _, d in onset_train)
    onset['identified'] = onset['training_onsets'] >= 3 and max(c[0] for c in candidates) - best[0] > 1e-6
  missing = ['A fresh low-speed autonomous response episode on the reserved route is absent.'] if not any(
    e['reserved'] and e['autonomous_low_seconds'] >= 1. for e in events) else []
  if onset is None or not onset['identified']:
    missing.append('At least three independent autonomous zero-pressure application transitions are needed to identify onset delay.')
  missing.append('Verify the meaning and actuation-state dependence of the raw pressure/regen signals before pooling manual and autonomous response.')
  result = {'version': 1, 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'sources': source_hashes(),
            'model_sha256': hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest(),
            'events': events, 'pressure_onset_experiment': onset,
            'regen_command_audit': regen_command_audit(training, evaluation_route),
            'physical_response': {source: physical_fit(training, source) for source in ('autonomous', 'manual')},
            'takeover_events': sum(e['kind'] == 'takeover' for e in events),
            'takeover_low_seconds': sum(int((masks(d)[0] & (d['vraw'] > .1) & (d['vraw'] < 2)).sum()) * DT
                                        for e, d in training if e['kind'] == 'takeover'),
            'training_low_seconds': sum(int((masks(d)[0] & (d['vraw'] > .1) & (d['vraw'] < 2)).sum()) * DT for _, d in training),
            'missing_measurements': missing,
            'limitations': ['Pressure is raw CAN data. Regen raw zero is a selection condition, not a calibrated torque measurement.',
                            'Measured-speed diagnostic stages isolate error; full free-running reproduction remains mandatory.',
                            'Manual observations identify measured-pressure response only; they never fit command-to-pressure response.',
                            'No new brake CAN mode is enabled. Mode 0xb remains disabled.',
                            'The onset experiment retains the earlier pooled modulation fit; its evaluation is not fully independent of that fit.']}
  atomic_json(root / 'brake-diagnostics.json', result)
  print(json.dumps({k: v for k, v in result.items() if k != 'events'}, indent=2), flush=True)
  return result


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('review', type=Path)
  diagnose(parser.parse_args().review)
