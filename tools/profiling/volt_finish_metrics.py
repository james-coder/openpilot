"""Physical finish checks, separate from unchanged recorded-reproduction metrics."""

import numpy as np


def physical_finish(trace):
  missing = dict(confirmed_stop=None, creep_seconds=None, terminal_accel=None, terminal_jerk_p95=None)
  if not trace or any('physical_v' not in r or 'physical_accel' not in r for r in trace):
    return missing
  times = np.array([r['t'] for r in trace])
  speed = np.array([r['physical_v'] for r in trace])
  accel = np.array([r['physical_accel'] for r in trace])
  stops = [i for i in range(len(trace)) if speed[i] < .03 and times[-1] >= times[i]+.2
           and np.all(speed[(times >= times[i]) & (times <= times[i]+.2)] < .03)]
  if not stops:
    return missing
  # Last transition, so a preceding stop in a stop/resume scenario cannot pass.
  starts = [i for i in stops if i == 0 or speed[i-1] >= .03]
  if not starts or starts[-1] == 0:
    return missing
  end = starts[-1]
  low = end
  while low > 0 and speed[low-1] < .3:
    low -= 1
  # Differentiate the unconstrained force-based acceleration BEFORE the plant's
  # forward-speed clamp. Include only known samples, never pad the endpoint.
  jerk = (np.interp(times+.1, times, accel)-np.interp(times-.1, times, accel))/.2
  terminal = (times >= times[end]-.25) & (times <= times[end]) & (times >= times[0]+.1) & (times <= times[-1]-.1)
  return {'confirmed_stop': float(times[end]), 'creep_seconds': float(times[end]-times[low]),
          'terminal_accel': float(np.max(abs(accel[terminal]))) if terminal.any() else None,
          'terminal_jerk_p95': float(np.percentile(abs(jerk[terminal]), 95)) if terminal.any() else None}
