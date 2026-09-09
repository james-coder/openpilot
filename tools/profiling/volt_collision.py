"""Hypothetical collision-envelope experiments; no vehicle access."""

from dataclasses import asdict, replace
from openpilot.selfdrive.controls.lib.volt_collision import Envelope, assess, maximum_closure, motion  # noqa: F401

def experiments():
  """Reproducible synthetic comparisons, explicitly separate from vehicle proof."""
  base = Envelope(.5, 4., 8., 1., .5, .15, 2.5)
  cases = [('Walking-speed approach', 3., 2., 0., base),
           ('Stationary vehicle, stronger than comfort needed', 24., 10., 0., base),
           ('Stationary vehicle, already outside braking envelope', 10., 10., 0., base),
           ('Lead suddenly brakes', 30., 15., 15., base),
           ('Reduced braking capability', 24., 10., 0., replace(base, ego_brake=2., comfort_brake=2.)),
           ('Longer response delay', 24., 10., 0., replace(base, response_seconds=1.)),
           ('Already stationary', 1., 0., 0., replace(base, acceleration_during_response=0.))]
  return {'version': 1, 'runtime_connected': False, 'vehicle_calibrated': False,
          'note': 'Offline analytical experiments. Positive clearance depends on assumed detection, geometry, delay and braking bounds; '
                  + 'these are not measured emergency performance and do not establish crash protection.',
          'cases': [dict(name=name, gap=gap, ego_speed=ego, lead_speed=lead, age=.1, assumptions=asdict(bounds),
                         result=assess(gap, ego, lead, .1, bounds, in_path=True, confirmed=True))
                    for name, gap, ego, lead, bounds in cases]}
