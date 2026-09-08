"""Host-only pressure/regen identification. Raw CAN pressure has no physical unit.

Separates command-to-pressure timing from pressure-to-acceleration response.
Training and evaluation routes are explicit; no values are installed in the car.
"""

from collections import deque
from dataclasses import replace
from itertools import product
import numpy as np
from scipy.optimize import least_squares, lsq_linear
from scipy.signal import lfilter


def allocator_profile(fit):
  """Offline inverse of the training fit; never persisted to an on-device profile."""
  from opendbc.car.gm.volt_longitudinal import PROFILE, profile_valid
  p, c = fit['pressure'], fit['coefficients']
  speed = PROFILE.speed
  profile = replace(PROFILE,
                    brake_gain_speed=tuple(c[1] * (p['gain'] + p.get('gain_low', 0) * max(0, 1 - v / 3)) / 400 for v in speed),
                    brake_deadband=p['deadband'] * 400,
                    brake_power=p.get('power', 1.),
                    regen=tuple(float(min(1.5, c[2]) * np.interp(v, fit['regen_fade_speed'], [0, 1])) for v in speed),
                    creep=tuple(max(0, c[3] * max(0, 1 - v / 2) + c[4]) for v in speed))
  if not profile_valid(profile):
    raise ValueError('Pressure fit is outside candidate calibration bounds')
  return profile


def hold_sample(times, values, requested):
  """Causal zero-order hold, with NaN before the first known observation."""
  pos = np.searchsorted(times, requested, side='right') - 1
  return np.where(pos >= 0, np.asarray(values)[np.maximum(pos, 0)], np.nan)


def pressure_trace(command, dt, delay, rise, release, gain, deadband, gain_low=0.0, power=1.0, speed=None, initial=0.0):
  delayed = np.nan_to_num(hold_sample(np.arange(len(command)) * dt, command, np.arange(len(command)) * dt - delay))
  speed_gain = gain + gain_low * np.clip(1 - np.asarray(speed if speed is not None else np.zeros(len(command))) / 3., 0, 1)
  exponent = 1 + (power - 1) * np.clip(np.asarray(speed if speed is not None else np.zeros(len(command))) / 3., 0, 1)
  target = speed_gain * np.maximum(0, delayed - deadband) ** exponent
  result = np.empty(len(command))
  pressure = initial
  for i, value in enumerate(target):
    pressure += dt / ((rise if value > pressure else release) + dt) * (value - pressure)
    result[i] = pressure
  return result


def fit_pressure_response(train, test, train_route, test_route):
  dt = float(np.median(np.diff(train['t'])))
  mask = train['mask'] & train['pressure_valid'] & (train['v'] > 0.1)
  if mask.sum() < 100:
    raise ValueError('Insufficient autonomous pressure observations')
  candidates = []
  low_weight = min(8., np.sqrt(mask.sum() / max(1, (mask & (train['v'] < 2)).sum())))
  weights = np.where(train['v'][mask] < 2, low_weight, 1.)
  # Delay is a discrete grid: a finite-difference optimizer cannot differentiate ZOH jumps.
  for delay in (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6):
    def residual(x, delay=delay):
      return (pressure_trace(train['brake'], dt, delay, *x, speed=train['v']) - train['pressure'])[mask] * weights
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
  predicted_pressure = pressure_trace(train['brake'], dt, delay, rise, release, gain, deadband, gain_low, power, speed=train['v'])
  for regen_delay, fade_start, fade_end in product((0., .1, .2, .3, .5), (0., .5, 1.5), (3., 5., 8., 12.)):
    X = response_features(train, regen_delay, fade_start, fade_end)
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
    episodes = np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    for episode in episodes:
      if len(episode) < 20:
        continue
      # Independent initial velocity per observed episode; never integrate
      # across a pedal intervention or a telemetry gap.
      integral = np.cumsum((predicted_X[episode][1:] + predicted_X[episode][:-1]) * dt / 2., axis=0)
      gravity = np.cumsum((np.sin(train['pitch'][episode][1:]) + np.sin(train['pitch'][episode][:-1])) * 9.81 * dt / 2.)
      velocity = train.get('vraw', train['v'])[episode]
      blocks.append(integral * .5)
      targets.append((velocity[1:] - velocity[0] + gravity) * .5)
    design, observed = np.vstack(blocks), np.concatenate(targets)
    coefficient = lsq_linear(design, observed,
                            bounds=([0.2, 0.2, 0.0, 0.0, -0.3], [4.0, 8.0, 2.0, 0.6, 0.3])).x
    error = design @ coefficient - observed
    best.append((float(np.mean(error ** 2)), regen_delay, coefficient, fade_start, fade_end))
  _, regen_delay, coefficient, fade_start, fade_end = min(best, key=lambda c: c[0])

  result = {
    'version': 2, 'train_route': train_route, 'holdout_route': test_route,
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


def response_features(data, regen_delay, fade_start, fade_end):
  step = float(np.median(np.diff(data['t'])))
  alpha = step / (0.1 + step)
  regen = np.nan_to_num(hold_sample(data['t'], data['regen'], data['t'] - regen_delay))
  regen = lfilter([alpha], [1, -(1 - alpha)], regen)
  gas = lfilter([alpha], [1, -(1 - alpha)], data['gas'])
  # Low-order monotone fade, rather than an independently fitted knot at every speed.
  capacity = np.clip((data['v'] - fade_start) / (fade_end - fade_start), 0, 1)
  return np.column_stack([gas, -data['pressure'], -regen * capacity, np.maximum(0, 1 - data['v'] / 2), np.ones(len(gas))])


def evaluate_pressure_response(data, fit):
  p = fit['pressure']
  delay, rise, release, gain, deadband = (p[k] for k in ('delay', 'rise', 'release', 'gain', 'deadband'))
  gain_low, power = p.get('gain_low', 0.), p.get('power', 1.)
  regen_delay, (fade_start, fade_end), coefficient = fit['regen_delay'], fit['regen_fade_speed'], np.asarray(fit['coefficients'])
  step = float(np.median(np.diff(data['t'])))
  m = data['mask'] & data['pressure_valid'] & data['pitch_valid'] & data['physical_a_valid'] & data.get('engine_valid', True) & ~data['engine']
  p = pressure_trace(data['brake'], step, delay, rise, release, gain, deadband, gain_low, power, speed=data['v'])
  measured_features = response_features(data, regen_delay, fade_start, fade_end)
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
    self.pressure = self.gas = self.regen = 0.0
    self.hold_margin = 0.
    self.brakes = deque([0.0] * round((fit['pressure']['delay'] + extra_delay) / dt))
    self.regens = deque([0.0] * round((fit['regen_delay'] + extra_delay) / dt))

  def step(self, gas, brake, speed, pitch=0.0, regen_factor=1.0):
    self.brakes.append(brake / 400.0)
    command = self.brakes.popleft()
    self.regens.append(max(0, -gas) / 650.0)
    regen = self.regens.popleft()
    p = self.fit['pressure']
    gain = p['gain'] + p.get('gain_low', 0.) * max(0, 1 - speed / 3.)
    exponent = 1 + (p.get('power', 1.) - 1) * min(1., speed / 3.)
    desired = gain * max(0, command - p['deadband']) ** exponent
    tau = p['rise'] if desired > self.pressure else p['release']
    self.pressure += self.dt / (tau + self.dt) * (desired - self.pressure)
    self.gas += self.dt / (0.1 + self.dt) * (max(0, gas) / 1018.0 - self.gas)
    self.regen += self.dt / (0.1 + self.dt) * (regen - self.regen)
    c = self.fit['coefficients']
    capacity = float(np.interp(speed, self.fit['regen_fade_speed'], [0, 1]))
    self.hold_margin = c[1] * self.pressure - abs(c[0] * self.gas + c[3] + c[4] - 9.81 * np.sin(pitch))
    return c[0] * self.gas - c[1] * self.pressure - c[2] * self.regen * capacity * regen_factor + c[3] * max(0, 1 - speed / 2) + c[4] - 9.81 * np.sin(pitch)
