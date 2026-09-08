"""Host-only pressure/regen identification. Raw CAN pressure has no physical unit.

Separates command-to-pressure timing from pressure-to-acceleration response.
Training and evaluation routes are explicit; no values are installed in the car.
"""

from collections import deque
from dataclasses import replace
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
                    regen=tuple(float(c[2] * np.interp(v, fit['regen_fade_speed'], [0, 1])) for v in speed),
                    creep=tuple(max(0, c[3] * max(0, 1 - v / 2) + c[4]) for v in speed))
  if not profile_valid(profile):
    raise ValueError('Pressure fit is outside candidate calibration bounds')
  return profile


def hold_sample(times, values, requested):
  """Causal zero-order hold, with NaN before the first known observation."""
  pos = np.searchsorted(times, requested, side='right') - 1
  return np.where(pos >= 0, np.asarray(values)[np.maximum(pos, 0)], np.nan)


def pressure_trace(command, dt, delay, rise, release, gain, deadband, gain_low=0.0, speed=None, initial=0.0):
  delayed = np.nan_to_num(hold_sample(np.arange(len(command)) * dt, command, np.arange(len(command)) * dt - delay))
  speed_gain = gain + gain_low * np.clip(1 - np.asarray(speed if speed is not None else np.zeros(len(command))) / 3., 0, 1)
  target = speed_gain * np.maximum(0, delayed - deadband)
  result = np.empty(len(command))
  pressure = initial
  for i, value in enumerate(target):
    pressure += dt / ((rise if value > pressure else release) + dt) * (value - pressure)
    result[i] = pressure
  return result


def fit_pressure_response(train, test, train_route, test_route):
  dt = float(np.median(np.diff(train['t'])))
  mask = train['mask'] & train['pressure_valid'] & (train['v'] > 0.4)
  if mask.sum() < 100:
    raise ValueError('Insufficient autonomous pressure observations')
  candidates = []
  low_weight = min(8., np.sqrt(mask.sum() / max(1, (mask & (train['v'] < 2)).sum())))
  weights = np.where(train['v'][mask] < 2, low_weight, 1.)
  # Delay is a discrete grid: a finite-difference optimizer cannot differentiate ZOH jumps.
  for delay in (0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6):
    def residual(x, delay=delay):
      return (pressure_trace(train['brake'], dt, delay, *x, speed=train['v']) - train['pressure'])[mask] * weights
    fit = least_squares(residual, [0.15, 0.08, 1.0, 0.02, 1.0], bounds=([0.02, 0.02, 0.1, 0.0, 0.0], [1.0, 1.0, 6.0, 0.15, 6.0]),
                        loss='soft_l1', f_scale=0.1, max_nfev=45)
    candidates.append((float(np.mean(residual(fit.x) ** 2)), delay, fit.x, bool(fit.success)))
  _, delay, x, converged = min(candidates, key=lambda c: c[0])
  rise, release, gain, deadband, gain_low = (float(v) for v in x)

  def features(data, regen_delay):
    step = float(np.median(np.diff(data['t'])))
    alpha = step / (0.1 + step)
    regen = np.nan_to_num(hold_sample(data['t'], data['regen'], data['t'] - regen_delay))
    regen = lfilter([alpha], [1, -(1 - alpha)], regen)
    gas = lfilter([alpha], [1, -(1 - alpha)], data['gas'])
    # Low-order monotone fade, rather than an independently fitted knot at every speed.
    capacity = np.clip((data['v'] - 0.5) / 4.5, 0, 1)
    return np.column_stack([gas, -data['pressure'], -regen * capacity, np.maximum(0, 1 - data['v'] / 2), np.ones(len(gas))])

  physical_mask = mask & train['pitch_valid'] & train['physical_a_valid'] & ~train['engine']
  if physical_mask.sum() < 100:
    raise ValueError('Insufficient calibrated engine-off response observations')
  best = []
  for regen_delay in (0.0, 0.1, 0.2, 0.3, 0.5):
    X = features(train, regen_delay)
    target = train['physical_a'] + 9.81 * np.sin(train['pitch'])
    weights = np.ones(physical_mask.sum())
    for _ in range(3):
      coefficient = lsq_linear(X[physical_mask] * weights[:, None], target[physical_mask] * weights,
                              bounds=([0.2, 0.2, 0.0, 0.0, -0.3], [4.0, 8.0, 2.0, 0.6, 0.3])).x
      error = X[physical_mask] @ coefficient - target[physical_mask]
      weights = np.sqrt(np.minimum(1.0, 0.3 / np.maximum(abs(error), 1e-6)))
    best.append((float(np.mean(error ** 2)), regen_delay, coefficient))
  _, regen_delay, coefficient = min(best, key=lambda c: c[0])

  def evaluate(data):
    step = float(np.median(np.diff(data['t'])))
    m = data['mask'] & data['pressure_valid'] & data['pitch_valid'] & data['physical_a_valid'] & ~data['engine']
    p = pressure_trace(data['brake'], step, delay, rise, release, gain, deadband, gain_low, speed=data['v'])
    measured_features = features(data, regen_delay)
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

  return {
    'version': 1, 'train_route': train_route, 'holdout_route': test_route,
    'pressure': {'delay': delay, 'rise': rise, 'release': release, 'gain': gain, 'gain_low': gain_low,
                 'deadband': deadband, 'converged': converged, 'low_speed_fit_weight': low_weight},
    'regen_delay': regen_delay, 'regen_fade_speed': [0.5, 5.0], 'coefficients': coefficient.tolist(), 'acceleration_frame': 'calibrated_vehicle_imu',
    'train': evaluate(train), 'holdout': evaluate(test), 'validated': False,
    'limitations': ['Raw pressure is normalized by 30000, not converted to physical units.',
                   'Engine-on behavior and battery-limited regen remain uncalibrated.',
                   'The fixed monotone regen fade is a low-order hypothesis; low-speed support remains sparse.',
                   'Previously inspected September 7 routes are regression data, not a new blind holdout.'],
  }


class PressureDynamics:
  def __init__(self, fit, dt=0.01, extra_delay=0.0):
    self.fit, self.dt = fit, dt
    self.pressure = self.gas = self.regen = 0.0
    self.brakes = deque([0.0] * round((fit['pressure']['delay'] + extra_delay) / dt))
    self.regens = deque([0.0] * round((fit['regen_delay'] + extra_delay) / dt))

  def step(self, gas, brake, speed, pitch=0.0, regen_factor=1.0):
    self.brakes.append(brake / 400.0)
    command = self.brakes.popleft()
    self.regens.append(max(0, -gas) / 650.0)
    regen = self.regens.popleft()
    p = self.fit['pressure']
    gain = p['gain'] + p.get('gain_low', 0.) * max(0, 1 - speed / 3.)
    desired = gain * max(0, command - p['deadband'])
    tau = p['rise'] if desired > self.pressure else p['release']
    self.pressure += self.dt / (tau + self.dt) * (desired - self.pressure)
    self.gas += self.dt / (0.1 + self.dt) * (max(0, gas) / 1018.0 - self.gas)
    self.regen += self.dt / (0.1 + self.dt) * (regen - self.regen)
    c = self.fit['coefficients']
    capacity = float(np.interp(speed, self.fit['regen_fade_speed'], [0, 1]))
    return c[0] * self.gas - c[1] * self.pressure - c[2] * self.regen * capacity * regen_factor + c[3] * max(0, 1 - speed / 2) + c[4] - 9.81 * np.sin(pitch)
