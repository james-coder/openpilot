"""Project logged radar tracks onto road-camera frames, to see which object each track really is. Read-only.

  .venv/bin/python -m openpilot.tools.dashcam.radar_overlay <incident-dir> <route> <seg-for-calibration> <onset-mono-s> <out.jpg> [rel-times...]

Geometry: road frame is x forward, y left, z up with its origin on the ground under the camera
(common/transformations/camera.py). Radar tracks are placed at x = sqrt(range^2 - yRel^2) + 1.52 m (the radar sits
about 1.52 m ahead of the camera, as in radard.RADAR_TO_CAMERA), y = yRel (positive left), and 0.7 m above the ground.
Calibration comes from the log's liveCalibration. Nose-dive under braking and the unknown height of each return mean
the circles are approximate; they identify objects, they do not measure them.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from openpilot.common.transformations.camera import get_view_frame_from_road_frame
from openpilot.tools.dashcam.frames import grab, locate
from openpilot.tools.dashcam.telemetry import read_events

K = np.array([[2648, 0, 964], [0, 2648, 604], [0, 0, 1.]])  # road camera, OX03C10 (DEVICE_CAMERAS fcam)
RADAR_TO_CAMERA = 1.52
COLORS = [(255, 80, 80), (255, 160, 0), (80, 200, 255), (255, 0, 255), (120, 255, 120), (255, 255, 0), (200, 200, 200), (255, 255, 255)]
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def calibration(rlog: Path):
  cal = None
  for ev in read_events(rlog):
    if ev.which() == 'liveCalibration' and len(ev.liveCalibration.rpyCalib) == 3:
      cal = (list(ev.liveCalibration.rpyCalib), list(ev.liveCalibration.height))
  return cal


def make_projector(rpy, height):
  E = get_view_frame_from_road_frame(*rpy, height)

  def proj(x_radar, y_left, z_up=0.7):
    v = E @ np.array([x_radar + RADAR_TO_CAMERA, y_left, z_up, 1.0])
    if v[2] < 0.5:
      return None
    u = K @ v
    return u[0] / u[2], u[1] / u[2]
  return proj


def sheet(inc: Path, route: str, cal_seg: int, onset: float, out: Path, rel, trk, enc):
  rpy, h = calibration(inc / 'raw' / f'{route}--{cal_seg}' / 'rlog.zst')
  proj = make_projector(rpy, h[0] if h else 1.22)
  locs = [locate(enc, onset + r) for r in rel]
  wanted = {}
  for s, i, _ in locs:
    wanted.setdefault(s, set()).add(i)
  frames = grab(inc / 'raw', route, 'road', wanted)
  W, H, cols = 640, 401, 3
  rows = (len(rel) + cols - 1) // cols
  im_out = Image.new('RGB', (W * cols, H * rows))
  big, small = ImageFont.truetype(FONT, 34), ImageFont.truetype(FONT, 15)
  for k, (r, (s, i, tt)) in enumerate(zip(rel, locs, strict=False)):
    im = Image.fromarray(frames[(s, i)])
    dr = ImageDraw.Draw(im)
    for row in trk[(np.abs(trk[:, 0] - tt) < 0.06) & (trk[:, 2] < 45)]:
      x = float(np.sqrt(max(row[2] ** 2 - row[3] ** 2, 0)))
      p = proj(x, row[3])
      if p and -50 < p[0] < 1980 and -50 < p[1] < 1260:
        c = COLORS[int(row[1]) % len(COLORS)]
        dr.ellipse([p[0] - 18, p[1] - 18, p[0] + 18, p[1] + 18], outline=c, width=5)
        dr.text((p[0] + 22, p[1] - 12), f'{int(row[1])}: {row[2]:.1f}m', fill=c, font=big)
    im = im.resize((W, H))
    d2 = ImageDraw.Draw(im)
    d2.rectangle([0, 0, 215, 24], fill=(0, 0, 0))
    d2.text((6, 3), f't{r:+.1f}s radar on road cam', fill=(255, 255, 0), font=small)
    im_out.paste(im, ((k % cols) * W, (k // cols) * H))
  im_out.save(out, quality=88)


if __name__ == '__main__':
  inc, route, seg, onset, out = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), Path(sys.argv[5])
  rel = [float(x) for x in sys.argv[6:]] or [-0.8, -0.4, 0.0, 0.2, 0.4, 1.0, 1.5, 2.0, 3.0]
  d = dict(np.load(inc / 'report' / 'telemetry.npz'))
  sheet(inc, route, seg, onset, out, rel, d['trk'], d['enc_road'])
