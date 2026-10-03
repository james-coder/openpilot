"""Replay logged carState + accelerometer through the dashcam event detector. Read-only.

  .venv/bin/python -m openpilot.tools.dashcam.detector_replay <telemetry.npz> [reference-mono-s]

Prints every episode the detector would have opened and closed, so thresholds can be checked against real drives.
"""
import sys

import numpy as np

from openpilot.system.review.detector import EventDetector


def replay(d: dict, det: EventDetector | None = None):
  det = det or EventDetector()
  cs, acc = d['cs'], d['acc']
  i = j = 0
  events = []
  while i < len(cs) or j < len(acc):
    take_cs = j >= len(acc) or (i < len(cs) and cs[i, 0] <= acc[j, 0])
    if take_cs:
      events += det.car_state(float(cs[i, 0]), float(cs[i, 1]), bool(cs[i, 5] > .5))
      i += 1
    else:
      events += det.accel(float(acc[j, 0]), *map(float, acc[j, 1:4]))
      j += 1
  events += det.tick(float(max(cs[-1, 0], acc[-1, 0])) + 10)
  return events


if __name__ == '__main__':
  d = dict(np.load(sys.argv[1]))
  ref = float(sys.argv[2]) if len(sys.argv) > 2 else d['cs'][0, 0]
  ev = replay(d)
  print(f'{len(ev)} events over {(d["cs"][-1, 0] - d["cs"][0, 0]) / 60:.1f} min of driving data')
  for e in ev:
    peaks = ' '.join(f'{k}={v:.2f}' for k, v in e['peaks'].items())
    print(f"  {e['type']:6s} t={e['t'] - ref:+8.2f}s kinds={','.join(e['kinds'])} {peaks}")
