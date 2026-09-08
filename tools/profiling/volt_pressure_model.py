"""Host-only pressure/regen identification. Raw CAN pressure has no physical unit.

Separates command-to-pressure timing from pressure-to-acceleration response.
Training and evaluation routes are explicit; no values are installed in the car.
"""

from dataclasses import replace
from itertools import product
import numpy as np
from scipy.optimize import least_squares, lsq_linear
from scipy.signal import lfilter
from openpilot.tools.profiling.volt_actuator import DelayedResponse, lag_update
from openpilot.tools.profiling.volt_observations import episodes


def allocator_profile(fit):
  """Offline inverse of the training fit; never persisted to an on-device profile."""
  from opendbc.car.gm.volt_longitudinal import PROFILE, profile_valid
  p, c = fit['pressure'], fit['coefficients']
  speed = PROFILE.speed
  coupled = c[5] if len(c) > 5 else 0.
  regen_bound = min(1., 1.5 / max(.001, c[2] + coupled))
  profile = replace(PROFILE,
                    brake_gain_speed=tuple(c[1] * (p['gain'] + p.get('gain_low', 0) * max(0, 1 - v / 3)) / 400 for v in speed),
                    brake_deadband=p['deadband'] * 400,
                    brake_power=p.get('power', 1.),
                    response_horizon=min(.6, p['delay'] + 3 * p['rise'] + .08),
                    regen=tuple(float(c[2] * regen_bound * np.interp(v, fit['regen_fade_speed'], [0, 1])) for v in speed),
                    brake_regen=tuple(float(coupled * regen_bound * np.interp(v, fit['regen_fade_speed'], [0, 1])) for v in speed),
                    creep=tuple(max(0, c[3] * max(0, 1 - v / 2) + c[4]) for v in speed))
  if not profile_valid(profile):
    raise ValueError('Pressure fit is outside candidate calibration bounds')
  return profile


def hold_sample(times, values, requested):
  """Causal zero-order hold, with NaN before the first known observation."""
  pos = np.searchsorted(times, np.asarray(requested) + 1e-9, side='right') - 1
  return np.where(pos >= 0, np.asarray(values)[np.maximum(pos, 0)], np.nan)


def pressure_trace(command, dt, delay, rise, release, gain, deadband, gain_low=0.0, power=1.0, speed=None, initial=0.0):
  lag = DelayedResponse(delay, rise, release, initial)
  speeds = speed if speed is not None else np.zeros(len(command))
  return np.array([lag.step(float(cmd), dt, pressure_target(v, gain, gain_low, deadband, power))
                   for cmd, v in zip(command, speeds, strict=True)])


def pressure_target(speed, gain, gain_low=0., deadband=0., power=1.):
  fraction = min(1., abs(float(speed)) / 3.)
  multiplier, exponent = gain + gain_low * (1 - fraction), 1 + (power - 1) * fraction
  return lambda command: multiplier * max(0., command - deadband) ** exponent


def episode_pressure(data, parameters):
  """Observed initial pressure and actual delay history, separately per episode."""
  output = np.full(len(data['t']), np.nan)
  p = parameters
  for part in episodes(data):
    times = data['t'][part]
    start = int(np.searchsorted(times, times[0] + p['delay'] + 1e-8))
    if start >= len(part):
      continue
    first = part[start]
    lag = DelayedResponse(p['delay'], p['rise'], p['release'])
    lag.seed(data['t'][first], list(zip(times[:start+1], data['brake'][part[:start+1]], strict=True)), data['pressure'][first])
    output[first] = lag.value
    for before, after in zip(part[start:-1], part[start+1:], strict=True):
      output[after] = lag.step(data['brake'][before], data['t'][after] - data['t'][before],
                               pressure_target(data['vraw'][before], p['gain'], p.get('gain_low', 0.), p['deadband'], p.get('power', 1.)))
  return output


def episode_lag(data, key, delay, tau=.1):
  output = np.full(len(data['t']), np.nan)
  for part in episodes(data):
    if len(part) < 2:
      continue
    times = data['t'][part]
    delayed = hold_sample(times, data[key][part], times - delay)
    known = np.flatnonzero(np.isfinite(delayed))
    if not len(known):
      continue
    start = known[0]
    # Unknown filter state is bounded by [0,1]; the unscored warm-up permits
    # six time constants AFTER the largest delay before fitting any residual.
    alpha = lag_update(0., 1., float(np.median(np.diff(times))), tau, tau)
    output[part[start]] = 0.
    output[part[start+1:]] = lfilter([alpha], [1, -(1-alpha)], delayed[start:-1])
  return output


def fit_pressure_response(train, test, train_route, test_route):
  mask = train['mask'] & train['pressure_valid'] & (train['v'] > 0.1)
  if mask.sum() < 100:
    raise ValueError('Insufficient autonomous pressure observations')
  candidates = []
  low_weight = min(8., np.sqrt(mask.sum() / max(1, (mask & (train['v'] < 2)).sum())))
  weights = np.where(train['v'][mask] < 2, low_weight, 1.)
  # Delay is a discrete grid: a finite-difference optimizer cannot differentiate ZOH jumps.
  for delay in (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6):
    def residual(x, delay=delay):
      parameters = dict(zip(('rise', 'release', 'gain', 'deadband', 'gain_low', 'power'), x, strict=True), delay=delay)
      return (episode_pressure(train, parameters) - train['pressure'])[mask] * weights
    fit = least_squares(residual, [0.15, 0.08, 1.0, 0.02, 1.0, 1.5],
                        bounds=([0.02, 0.02, 0.1, 0.0, 0.0, 1.], [1.0, 1.0, 6.0, 0.15, 6.0, 3.]),
                        loss='linear', max_nfev=60)
    candidates.append((float(np.mean(residual(fit.x) ** 2)), delay, fit.x, bool(fit.success)))
  _, delay, x, converged = min(candidates, key=lambda c: c[0])
  rise, release, gain, deadband, gain_low, power = (float(v) for v in x)

  physical_mask = mask & train['pitch_valid'] & train['physical_a_valid'] & train.get('engine_valid', True) & ~train['engine']
  if physical_mask.sum() < 100:
    raise ValueError('Insufficient calibrated engine-off response observations')
  best = []
  parameters = dict(delay=delay, rise=rise, release=release, gain=gain, deadband=deadband, gain_low=gain_low, power=power)
  predicted_pressure = episode_pressure(train, parameters)
  filtered = {delay: (episode_lag(train, 'gas', 0.), episode_lag(train, 'regen', delay), episode_lag(train, 'brake', delay))
              for delay in (0., .1, .2, .3, .5)}
  for regen_delay, fade_start, fade_end in product((0., .1, .2, .3, .5), (0., .5, 1.5), (3., 5., 8., 12.)):
    X = response_features(train, regen_delay, fade_start, fade_end, filtered[regen_delay])
    target = train['physical_a'] + 9.81 * np.sin(train['pitch'])
    predicted_X = X.copy()
    predicted_X[:, 1] = -predicted_pressure
    # Fit transient response and whole-episode speed change together. Large
    # braking events are the behavior of interest, not outliers to discard.
    weights = 1 + 2 * np.clip(-train['physical_a'][physical_mask] - 1., 0., 2.)
    weights *= np.where(train['v'][physical_mask] < 2., low_weight, 1.)
    blocks = [X[physical_mask] * weights[:, None], predicted_X[physical_mask] * weights[:, None]]
    targets = [target[physical_mask] * weights, target[physical_mask] * weights]
    indices = np.flatnonzero(physical_mask)
    pieces = np.split(indices, np.flatnonzero((np.diff(indices) != 1) | (np.diff(train['episode'][indices]) != 0)) + 1)
    for episode in pieces:
      if len(episode) < 20:
        continue
      # Independent initial velocity per observed episode; never integrate
      # across a pedal intervention or a telemetry gap.
      steps = np.diff(train['t'][episode])
      integral = np.cumsum((predicted_X[episode][1:] + predicted_X[episode][:-1]) * steps[:, None] / 2., axis=0)
      gravity = np.cumsum((np.sin(train['pitch'][episode][1:]) + np.sin(train['pitch'][episode][:-1])) * 9.81 * steps / 2.)
      velocity = train.get('vraw', train['v'])[episode]
      blocks.append(integral * .5)
      targets.append((velocity[1:] - velocity[0] + gravity) * .5)
    design, observed = np.vstack(blocks), np.concatenate(targets)
    coefficient = lsq_linear(design, observed,
                            bounds=([0.2, 0.2, 0.0, 0.0, -0.3, 0.], [4.0, 8.0, 2.0, 0.6, 0.3, 2.])).x
    error = design @ coefficient - observed
    best.append((float(np.mean(error ** 2)), regen_delay, coefficient, fade_start, fade_end))
  _, regen_delay, coefficient, fade_start, fade_end = min(best, key=lambda c: c[0])

  result = {
    'version': 4, 'kernel': 'timestamp-exponential-v1', 'train_route': train_route, 'holdout_route': test_route,
    'regen_model': 'gas-demand-plus-brake-command-v1',
    'objective': 'pressure, transient acceleration, and integrated episode wheel speed',
    'pressure': {'delay': delay, 'rise': rise, 'release': release, 'gain': gain, 'gain_low': gain_low,
                 'deadband': deadband, 'power': power, 'converged': converged, 'low_speed_fit_weight': low_weight},
    'regen_delay': regen_delay, 'regen_fade_speed': [fade_start, fade_end], 'coefficients': coefficient.tolist(),
    'acceleration_frame': 'calibrated_vehicle_imu',
    'validated': False,
    'limitations': ['Raw pressure is normalized by 30000, not converted to physical units.',
                   'Engine-on behavior and battery-limited regen remain uncalibrated.',
                   'A monotone regen fade is fitted within a bounded grid; low-speed support remains sparse.',
                   'Previously inspected September 7 routes are regression data, not a new blind holdout.'],
  }

  result.update(train=evaluate_pressure_response(train, result), holdout=evaluate_pressure_response(test, result))
  return result


def response_features(data, regen_delay, fade_start, fade_end, filtered=None, coupled=True):
  gas, regen, brake = filtered if filtered is not None else (
    episode_lag(data, 'gas', 0.), episode_lag(data, 'regen', regen_delay), episode_lag(data, 'brake', regen_delay))
  # Low-order monotone fade, rather than an independently fitted knot at every speed.
  speed = data.get('vraw', data['v'])
  capacity = np.clip((speed - fade_start) / (fade_end - fade_start), 0, 1)
  columns = [gas, -data['pressure'], -regen * capacity, np.maximum(0, 1 - speed / 2), np.ones(len(gas))]
  if coupled:
    columns.append(-brake * regen * capacity)
  return np.column_stack(columns)


def evaluate_pressure_response(data, fit):
  regen_delay, (fade_start, fade_end), coefficient = fit['regen_delay'], fit['regen_fade_speed'], np.asarray(fit['coefficients'])
  m = data['mask'] & data['pressure_valid'] & data['pitch_valid'] & data['physical_a_valid'] & data.get('engine_valid', True) & ~data['engine']
  p = episode_pressure(data, fit['pressure'])
  measured_features = response_features(data, regen_delay, fade_start, fade_end, coupled=len(coefficient) > 5)
  predicted_features = measured_features.copy()
  predicted_features[:, 1] = -p
  a = predicted_features @ coefficient - 9.81 * np.sin(data['pitch'])
  error = a - data['physical_a']
  low = m & (data['v'] < 2)
  return {
    'samples': int(m.sum()), 'low_speed_samples': int(low.sum()),
    'rmse': float(np.sqrt(np.mean(error[m] ** 2))) if m.any() else None,
    'low_speed_rmse': float(np.sqrt(np.mean(error[low] ** 2))) if low.any() else None,
    'pressure_rmse_raw': float(np.sqrt(np.mean((p[m] - data['pressure'][m]) ** 2)) * 30000) if m.any() else None,
    'measured_pressure_accel_rmse': float(np.sqrt(np.mean((measured_features @ coefficient - 9.81 * np.sin(data['pitch']) - data['physical_a'])[m] ** 2)))
    if m.any() else None,
  }



class PressureDynamics:
  def __init__(self, fit, dt=0.01, extra_delay=0.0):
    self.fit, self.dt = fit, dt
    p = fit['pressure']
    self.pressure_lag = DelayedResponse(p['delay'] + extra_delay, p['rise'], p['release'])
    self.gas_lag = DelayedResponse(0., .1)
    self.regen_lag = DelayedResponse(fit['regen_delay'] + extra_delay, .1)
    self.brake_regen_lag = DelayedResponse(fit['regen_delay'] + extra_delay, .1)
    self.hold_margin = 0.
    self.drive_accel = self.brake_capacity = 0.

  @property
  def pressure(self):
    return self.pressure_lag.value

  @pressure.setter
  def pressure(self, value):
    self.pressure_lag.value = value

  @property
  def gas(self):
    return self.gas_lag.value

  @property
  def regen(self):
    return self.regen_lag.value

  def step(self, gas, brake, speed, pitch=0.0, regen_factor=1.0):
    p = self.fit['pressure']
    self.pressure_lag.step(brake / 400., self.dt,
                           pressure_target(speed, p['gain'], p.get('gain_low', 0.), p['deadband'], p.get('power', 1.)))
    self.gas_lag.step(max(0, gas) / 1018., self.dt)
    self.regen_lag.step(max(0, -gas) / 650., self.dt)
    self.brake_regen_lag.step(brake / 400., self.dt)
    c = self.fit['coefficients']
    capacity = float(np.interp(abs(speed), self.fit['regen_fade_speed'], [0, 1]))
    self.drive_accel = c[0] * self.gas + c[3] * max(0, 1 - abs(speed) / 2) + c[4] - 9.81 * np.sin(pitch)
    regen_accel = (c[2] + (c[5] * self.brake_regen_lag.value if len(c) > 5 else 0.)) * self.regen * capacity * regen_factor
    self.brake_capacity = c[1] * self.pressure + regen_accel
    # Regeneration supplies no stationary holding authority.
    self.hold_margin = c[1] * self.pressure - abs(c[0] * self.gas + c[3] + c[4] - 9.81 * np.sin(pitch))
    return self.drive_accel - self.brake_capacity * (1 if speed >= 0 else -1)
