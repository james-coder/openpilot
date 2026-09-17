"""Fresh causal observations and continuous episodes for Volt response studies."""

import numpy as np

FIELDS = {
  'vraw': None, 'v': None, 'a': None, 'foot': None, 'pedal': None, 'gas': None, 'regen': None, 'valid': None,
  'steering_angle': None, 'standstill': None, 'active': 'carControl', 'applied_brake': 'carOutput', 'applied_gas': 'carOutput',
  'pressure': 'can_368', 'brake_mode': 'can_789', 'regen_raw': 'can_560', 'engine_rpm': 'engine',
  'controller_pitch': 'carControl', 'vehicle_ax': 'livePose',
}
WARMUP = 1.2  # Maximum fitted delay (0.6 s) plus six gas/regen time constants.


def sample_rows(rows, times):
  data = {'t': np.asarray(times)}
  for key, service in FIELDS.items():
    records = sorted({r.get('sample_times', {}).get(service, r['t']) if service else r['t']: float(r[key])
                      for r in rows if isinstance(r.get(key), (int, float, bool)) and np.isfinite(r[key])}.items())
    values = np.full(len(times), np.nan)
    if records:
      stamps, observed = np.asarray(records).T
      positions = np.searchsorted(stamps, times, side='right') - 1
      bounded = np.maximum(0, positions)
      age = times - stamps[bounded]
      fresh = (positions >= 0) & (age <= (.1 if service is None else .3) + 1e-6)
      values[fresh] = observed[bounded[fresh]]
    data[key] = values
  return data


def runs(mask):
  indices = np.flatnonzero(mask)
  return [p for p in np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1) if len(p)]


def episodes(data):
  ids = data.get('episode')
  if ids is None:
    return runs(data['mask'])
  return [p for p in np.split(np.arange(len(ids)), np.flatnonzero(np.diff(ids) != 0) + 1)
          if len(p) and ids[p[0]] >= 0]


def pedals_clear(data):
  # This gateway Volt's GM carState and panda both use raw BrakePedalPos >= 8.
  # Raw values 1..7 alone are not a decoded driver brake application. Still
  # require the independent brakePressed flag to be clear; unknowns fail closed.
  return ((data['foot'] == 0) & (data['pedal'] >= 0) & (data['pedal'] < 8)
          & (data['gas'] == 0) & (data['regen'] == 0))


def autonomous_mask(data, dt):
  clear = pedals_clear(data)
  width = 2 * round(1. / dt) + 1
  guarded = np.convolve((~clear).astype(int), np.ones(width), mode='same') == 0 if len(clear) >= width else np.zeros(len(clear), bool)
  return (guarded & (data['valid'] == 1) & (data['active'] == 1)
          & (abs(data['steering_angle']) < 25) & np.isin(data['brake_mode'], [1, 10, 13])
          & np.isfinite(data['applied_brake']) & np.isfinite(data['applied_gas'])
          & np.isfinite(data['vraw']) & np.isfinite(data['pressure']) & np.isfinite(data['controller_pitch']))
