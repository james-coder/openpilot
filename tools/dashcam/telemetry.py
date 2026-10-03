"""Extract an incident timeline from local rlogs. Read-only; never touches the car.

  .venv/bin/python -m openpilot.tools.dashcam.telemetry <raw-dir> <route> <first-seg> <last-seg> <out.npz>

Everything is on the car's monotonic clock (seconds). Series are plain numpy arrays so reports, viewers and
detectors can share them. Notes that matter when reading the numbers:
  * carState.wheelSpeeds is all zeros in this build, so per-wheel speeds are decoded from raw CAN 840/842 (bus 0).
  * radar dRel is the straight-line range from the radar (behind the front fascia), yRel = sin(azimuth) * range.
  * The accelerometer is rotated into the car frame from data (gravity at rest + regression against vEgo slope),
    not from liveCalibration, so it needs no assumption about sensor axis conventions.
"""
import re
import sys
from pathlib import Path

import numpy as np
import zstandard
from cereal import log

SEGMENT = re.compile(r'^(.+)--(\d+)$')
KMH = 1 / 3.6
WHEEL_SCALE = 0.0311  # km/h per count, opendbc gm_global_a_powertrain EBCMWheelSpdFront/Rear
G = 9.81


def read_events(path):
  with open(path, 'rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as s:
    raw = s.read()
  it = log.Event.read_multiple_bytes(raw)
  while True:
    try:
      yield next(it)
    except StopIteration:
      return
    except Exception:  # truncated final message
      return


def extract(raw_dir: Path, route: str, first: int, last: int) -> dict:
  cs, acc, gyr, wf, wr, lead, trk, mdl = [], [], [], [], [], [], [], []
  enc = {'road': [], 'wide': [], 'driver': []}
  gps = []
  for n in range(first, last + 1):
    path = raw_dir / f'{route}--{n}' / 'rlog.zst'
    if not path.exists():
      continue
    for ev in read_events(path):
      k, t = ev.which(), ev.logMonoTime / 1e9
      if k == 'carState':
        c = ev.carState
        cs.append((t, c.vEgo, c.vEgoRaw, c.aEgo, c.brake, c.brakePressed, c.gasPressed, c.steeringAngleDeg,
                   c.leftBlinker, c.rightBlinker, c.standstill))
      elif k == 'accelerometer':
        v = list(ev.accelerometer.acceleration.v)
        if len(v) == 3:
          acc.append((t, *v))
      elif k == 'gyroscope':
        v = list(ev.gyroscope.gyroUncalibrated.v) or list(ev.gyroscope.gyro.v)
        if len(v) == 3:
          gyr.append((t, *v))
      elif k == 'can':
        for m in ev.can:
          if m.src == 0 and len(m.dat) >= 4:
            if m.address == 840:
              wf.append((t, int.from_bytes(m.dat[0:2], 'big') * WHEEL_SCALE * KMH, int.from_bytes(m.dat[2:4], 'big') * WHEEL_SCALE * KMH))
            elif m.address == 842:
              wr.append((t, int.from_bytes(m.dat[0:2], 'big') * WHEEL_SCALE * KMH, int.from_bytes(m.dat[2:4], 'big') * WHEEL_SCALE * KMH))
      elif k == 'radarState':
        for slot, L in ((1, ev.radarState.leadOne), (2, ev.radarState.leadTwo)):
          if L.status:
            lead.append((t, slot, L.dRel, L.yRel, L.vRel, L.radar, L.modelProb))
      elif k == 'liveTracks':
        for p in ev.liveTracks.points:
          trk.append((t, p.trackId, p.dRel, p.yRel, p.vRel, p.measured, ev.valid))
      elif k == 'modelV2':
        ld = ev.modelV2.leadsV3
        if len(ld) and len(ld[0].x):
          mdl.append((t, ld[0].prob, ld[0].x[0], ld[0].v[0]))
      elif k in ('roadEncodeIdx', 'wideRoadEncodeIdx', 'driverEncodeIdx'):
        e = getattr(ev, k)
        enc[{'roadEncodeIdx': 'road', 'wideRoadEncodeIdx': 'wide', 'driverEncodeIdx': 'driver'}[k]].append(
          (n, e.segmentId, e.timestampSof / 1e9, e.timestampEof / 1e9))
      elif k in ('gpsLocationExternal', 'gpsLocation') and getattr(ev, k).unixTimestampMillis > 1.7e12:
        gps.append(getattr(ev, k).unixTimestampMillis / 1e3 - t)
  out = {k: np.array(v, dtype=float) for k, v in dict(cs=cs, acc=acc, gyr=gyr, wf=wf, wr=wr, lead=lead, trk=trk, mdl=mdl).items()}
  for k, v in enc.items():
    out['enc_' + k] = np.array(v, dtype=float)
  out['wall_offset'] = np.array([np.median(gps)]) if gps else np.array([np.nan])
  return out


def car_frame(d: dict) -> dict:
  """Rotate the IMU into the car frame from data and derive g series + a >10 Hz jolt trace."""
  acc, cs = d['acc'], d['cs']
  t, a = acc[:, 0], acc[:, 1:]
  v = np.interp(t, cs[:, 0], cs[:, 1])
  still = v < 0.05
  up = a[still].mean(0) if still.sum() > 50 else np.median(a, 0)
  gmag = float(np.linalg.norm(up))
  up = up / gmag  # accelerometers read +g upward at rest
  # forward axis: regress smoothed dv/dt (wheel-derived) against horizontal acceleration while moving
  tt, vv = cs[:, 0], cs[:, 1]
  dv = np.gradient(np.convolve(vv, np.ones(21) / 21, 'same'), tt)
  dvi = np.interp(t, tt, dv)
  ah = a - np.outer(a @ up, up)
  mov = (v > 2.0) & (np.abs(dvi) > 0.3)
  e1 = np.cross(up, [1.0, 0, 0] if abs(up[0]) < 0.9 else [0, 1.0, 0])
  e1 /= np.linalg.norm(e1)
  e2 = np.cross(up, e1)
  B = np.column_stack([ah[mov] @ e1, ah[mov] @ e2])
  w, *_ = np.linalg.lstsq(B, dvi[mov], rcond=None)
  fwd = w[0] * e1 + w[1] * e2
  fwd /= np.linalg.norm(fwd)
  left = np.cross(up, fwd)
  n = max(3, int(round(1 / np.median(np.diff(t)) / 10)) | 1)  # ~10 Hz high-pass window
  lp = np.column_stack([np.convolve(a[:, i], np.ones(n) / n, 'same') for i in range(3)])
  return dict(t=t, g_long=(ah @ fwd) / G, g_lat=(ah @ left) / G, g_vert=(a @ up - gmag) / G,
              g_horiz=np.linalg.norm(ah, axis=1) / G, jolt=np.linalg.norm(a - lp, axis=1) / G,
              fit_rms=float(np.sqrt(np.mean((B @ w - dvi[mov]) ** 2))), fit_n=int(mov.sum()), gravity=gmag,
              rate_hz=float(1 / np.median(np.diff(t))))


def wheel_slip(d: dict) -> dict:
  """ABS indicator: brake pressed while one wheel runs far below the fastest wheel. Calibrated on 32 normal
  segments (0 false alarms) plus the 2026-10-01 incident (the only hit); see docs in the plan."""
  wf, wr, cs = d['wf'], d['wr'], d['cs']
  t = wf[:, 0]
  W = np.column_stack([wf[:, 1], wf[:, 2], np.interp(t, wr[:, 0], wr[:, 1]), np.interp(t, wr[:, 0], wr[:, 2])])
  brake = np.interp(t, cs[:, 0], cs[:, 5]) > 0.5
  mx, mn = W.max(1), W.min(1)
  return dict(t=t, W=W, abs=brake & (mx > 2.0) & (mn < 0.5 * mx))


if __name__ == '__main__':
  raw, route, a, b, out = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
  data = extract(raw, route, a, b)
  np.savez_compressed(out, **data)
  print({k: v.shape for k, v in data.items()})
