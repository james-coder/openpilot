"""Cached event-level pressure and motion diagnosis. No device or CAN access."""

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import lsq_linear
from openpilot.tools.profiling.volt_braking import atomic_json
from openpilot.tools.profiling.volt_pressure_model import PressureDynamics

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
  data = {'t': t, 'ticks': ticks}
  for key, service in FIELDS.items():
    records = sorted({r.get('sample_times', {}).get(service, r['t']) if service else r['t']: float(r[key])
                      for r in rows if isinstance(r.get(key), (int, float, bool)) and np.isfinite(r[key])}.items())
    values = np.full(len(t), np.nan)
    if records:
      times, observed = np.asarray(records).T
      positions = np.searchsorted(times, t, side='right') - 1
      bounded = np.maximum(0, positions)
      fresh = (positions >= 0) & (t - times[bounded] <= (.1 if service is None else .3) + 1e-6)
      values[fresh] = observed[bounded[fresh]]
    data[key] = values
  return data


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
  pedals_clear = (data['foot'] == 0) & (data['pedal'] == 0) & (data['gas'] == 0) & (data['regen'] == 0)
  # Preserve the existing one-second intervention guard. Unknown inputs are not clear pedals.
  guarded = np.convolve((~pedals_clear).astype(int), np.ones(41), mode='same') == 0 if len(valid) >= 41 else np.zeros(len(valid), bool)
  autonomous = (valid & guarded & (data['active'] == 1) & np.isfinite(data['applied_brake'])
                & np.isfinite(data['applied_gas']) & np.isin(data['brake_mode'], [1, 10, 13]))
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
    first = part[0]
    dynamics = PressureDynamics(model, dt=DT)
    dynamics.pressure = data['pressure'][first] / 30000
    for i in range(len(dynamics.brakes)):
      dynamics.brakes[i] = data['applied_brake'][first] / 400
    previous_command = data['applied_brake'][first]
    onset = False
    wait = 0
    for i in part:
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
          dynamics.pressure = old_pressure + (dynamics.pressure - old_pressure) * (model['pressure']['rise'] + DT) / (onset_rise + DT)
      if command <= 0 or dynamics.pressure >= .05:
        onset = False
      output[i] = dynamics.pressure * 30000
  return output


def rmse(values):
  return float(np.sqrt(np.mean(np.square(values)))) if len(values) else None


def motion_error(data, predicted_pressure, model):
  autonomous, _ = masks(data)
  stages = []
  for part in runs(autonomous & (data['engine_rpm'] == 0) & (data['vraw'] > .1), minimum=20):
    dynamics = PressureDynamics(model, dt=DT)
    start = part[0]
    dynamics.regen = max(0., -data['applied_gas'][start] / 650)
    dynamics.gas = max(0., data['applied_gas'][start] / 1018)
    for k in range(len(dynamics.regens)):
      dynamics.regens[k] = dynamics.regen
    calculated, measured = [], []
    for i in part:
      a = dynamics.step(data['applied_gas'][i], data['applied_brake'][i], data['vraw'][i], data['controller_pitch'][i])
      other = a + model['coefficients'][1] * dynamics.pressure
      calculated.append(other - model['coefficients'][1] * predicted_pressure[i] / 30000)
      measured.append(other - model['coefficients'][1] * data['pressure'][i] / 30000)
    velocity = data['vraw'][part]
    def error(accel, velocity=velocity):
      predicted = velocity[0] + np.r_[0., np.cumsum((np.asarray(accel)[1:] + np.asarray(accel)[:-1]) * DT / 2)]
      return {'speed_rmse': rmse(predicted - velocity), 'end_speed_error': float(predicted[-1] - velocity[-1])}
    stages.append({'start': float(data['t'][start]), 'end': float(data['t'][part[-1]]),
                   'command_pressure_to_motion': error(calculated), 'measured_pressure_to_motion': error(measured)})
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
                   'motion_episodes': motion_error(d, predictions[e['id']], model),
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
            'model_sha256': hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest(),
            'events': events, 'pressure_onset_experiment': onset,
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
