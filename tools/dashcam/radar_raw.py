"""Print the radar unit's own object list, decoded from raw CAN, around a moment of interest. Read-only.

  .venv/bin/python -m openpilot.tools.dashcam.radar_raw <incident-dir> <route> <seg> [<seg>...] --at <log-mono-s> [--before 1.6] [--after 2.6]

This is what the radar sent on bus 1 (20 object slots plus the header with the target count and self-reported fault
bits), before openpilot's radar interface, radard or the logger touched it. If an object is missing here, the radar
never reported it; if it is present here but missing from liveTracks, our software dropped it.
"""
import argparse
from pathlib import Path

from opendbc.car.gm.radar_interface import LAST_RADAR_MSG, NUM_SLOTS, RADAR_HEADER_MSG, SLOT_1_MSG, create_radar_can_parser
from opendbc.car.gm.values import CAR

from openpilot.tools.dashcam.telemetry import read_events

FAULTS = ('FLRRSnsrBlckd', 'FLRRSnstvFltPrsntInt', 'FLRRYawRtPlsblityFlt', 'FLRRHWFltPrsntInt', 'FLRRAntTngFltPrsnt', 'FLRRAlgnFltPrsnt')


def cycles(raw_dir: Path, route: str, segs, t_a: float, t_b: float):
  """One row per radar cycle (the last object message arrives): (t, header target count, faults, [(id, range, az, rate, width)])."""
  rcp = create_radar_can_parser(CAR.CHEVROLET_VOLT)
  for seg in segs:
    for ev in read_events(raw_dir / f'{route}--{seg}' / 'rlog.zst'):
      if ev.which() != 'can':
        continue
      t = ev.logMonoTime / 1e9
      upd = rcp.update([(ev.logMonoTime, [(m.address, bytes(m.dat), m.src) for m in ev.can])])
      if LAST_RADAR_MSG not in upd or not t_a <= t <= t_b:
        continue
      h = rcp.vl[RADAR_HEADER_MSG]
      slots = []
      for m in range(SLOT_1_MSG, SLOT_1_MSG + NUM_SLOTS):
        c = rcp.vl[m]
        if c['TrkRange'] > 0:
          slots.append((int(c['TrkObjectID']), c['TrkRange'], c['TrkAzimuth'], c['TrkRangeRate'], c['TrkWidth']))
      yield t, int(h['FLRRNumValidTargets']), [k for k in FAULTS if h[k]], slots


if __name__ == '__main__':
  ap = argparse.ArgumentParser()
  ap.add_argument('incident')
  ap.add_argument('route')
  ap.add_argument('segs', type=int, nargs='+')
  ap.add_argument('--at', type=float, required=True)
  ap.add_argument('--before', type=float, default=1.6)
  ap.add_argument('--after', type=float, default=2.6)
  a = ap.parse_args()
  last = -1.
  print('rel_t   header-count faults   objects within 25 m: id:range m / azimuth deg / range-rate m/s')
  for t, n, f, s in cycles(Path(a.incident) / 'raw', a.route, a.segs, a.at - a.before, a.at + a.after):
    if t - last < 0.09:
      continue
    last = t
    near = ' '.join(f'{i}:{r:.1f}/{az:+.0f}/{rr:+.1f}' for i, r, az, rr, _ in sorted(s, key=lambda x: x[1]) if r < 25)
    print(f'{t - a.at:+5.2f}  {n:3d}  {",".join(x[4:12] for x in f) or "-":8s}  {near}')
