"""Synthetic footage for the Dashcam player tests: a tiny H.264 MPEG-TS clip and matching qlog, written with PyAV and cereal."""
import os
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import zstandard
from cereal import log

ROUTE = '00000007--abcdef1234'
FPS = 20


def frame_red(i: int) -> int:
  return min(255, 10 + 8 * i)


def make_qcamera(path, frames: int = 30, fps: int = FPS, gop: int = 10, size=(526, 330), painter=None) -> None:
  """Frame i is a flat colour whose red channel encodes i (see frame_red), so a test can tell which frame it decoded.
  painter(i) -> HxWx3 uint8 replaces that for previews that want a recognisable picture."""
  w, h = size
  with av.open(str(path), 'w', format='mpegts') as c:
    s = c.add_stream('libx264', rate=fps)
    s.width, s.height, s.pix_fmt = w, h, 'yuv420p'
    s.options = {'g': str(gop), 'bf': '0', 'preset': 'ultrafast', 'tune': 'zerolatency'}
    for i in range(frames):
      if painter is not None:
        img = painter(i)
      else:
        img = np.zeros((h, w, 3), np.uint8)
        img[:, :, 0] = frame_red(i)
        img[:, :, 1] = 128
        img[:, :, 2] = 64
      f = av.VideoFrame.from_ndarray(img, format='rgb24')
      f.pts, f.time_base = i, Fraction(1, fps)
      for p in s.encode(f):
        c.mux(p)
    for p in s.encode(None):
      c.mux(p)


def make_qlog(path, frame_monos, car=()) -> None:
  """qlog.zst with a qRoadEncodeIdx per frame and carState samples (mono s, vEgo, brake, left, right)."""
  out = []
  for i, t in enumerate(frame_monos):
    ev = log.Event.new_message()
    ev.logMonoTime = int(t * 1e9)
    e = ev.init('qRoadEncodeIdx')
    e.segmentId, e.timestampEof = i, int(t * 1e9)
    out.append(ev.to_bytes())
  for t, v, brake, left, right in car:
    ev = log.Event.new_message()
    ev.logMonoTime = int(t * 1e9)
    c = ev.init('carState')
    c.vEgo, c.brakePressed, c.leftBlinker, c.rightBlinker = v, bool(brake), bool(left), bool(right)
    out.append(ev.to_bytes())
  Path(path).write_bytes(zstandard.ZstdCompressor().compress(b''.join(out)))


def make_segment(root, n: int, *, frames: int = 30, mono0: float = 1000.0, route: str = ROUTE, qlog: bool = True, car=None, gop: int = 10,
                 size=(526, 330), painter=None) -> Path:
  """<root>/<route>--<n>/ with qcamera.ts and (optionally) qlog.zst. Segment n starts at mono0 + 60 s * n by default spacing of its own
  length, so neighbouring segments are contiguous in log time when frames = 60 * fps."""
  d = Path(root) / f'{route}--{n}'
  d.mkdir(parents=True, exist_ok=True)
  make_qcamera(d / 'qcamera.ts', frames, gop=gop, size=size, painter=painter)
  if qlog:
    monos = [mono0 + i / FPS for i in range(frames)]
    samples = car if car is not None else [(mono0 + k * 0.1, 10.0 + k * 0.1, k % 7 == 3, k % 11 == 5, False) for k in range(int(frames / FPS * 10))]
    make_qlog(d / 'qlog.zst', monos, samples)
  return d


def write_events(path, records) -> None:
  import json
  Path(path).parent.mkdir(parents=True, exist_ok=True)
  with open(path, 'w') as f:
    for r in records:
      f.write((r if isinstance(r, str) else json.dumps(r)) + '\n')


def event_records(segment: str, *, mono: float, wall: float = 1_790_000_000.0, ident: int = 1, kinds=('hard_brake',), peaks=None) -> list:
  peaks = peaks or {'decel_g': 0.62}
  return [dict(v=1, id=ident, phase='start', mono=mono, wall=wall, kinds=list(kinds), peaks=peaks, segment=segment),
          dict(v=1, id=ident, phase='end', mono=mono + 1, wall=wall + 4, kinds=list(kinds), peaks=peaks, segment=segment)]


def wait_until(cond, timeout: float = 8.0, step: float = 0.01) -> bool:
  import time
  end = time.monotonic() + timeout
  while time.monotonic() < end:
    if cond():
      return True
    time.sleep(step)
  return cond()


def can_write_xattr(path) -> bool:
  try:
    import xattr
    probe = Path(path) / '.probe'
    probe.mkdir(exist_ok=True)
    xattr.setxattr(str(probe), 'user.test', b'1')
    return True
  except Exception:
    return False


def listdir(path) -> list:
  return sorted(os.listdir(path))
