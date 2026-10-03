"""Exact-frame access to recorded HEVC video, keyed by log time. Read-only.

Frame i of <route>--<seg>/<camera>.hevc corresponds to the EncodeIdx entry whose segmentId == i; its capture time
is that entry's timestampEof on the log's monotonic clock (the same clock as every other message).
"""
from pathlib import Path

import av
import numpy as np

CAMERA_FILE = {'road': 'fcamera.hevc', 'wide': 'ecamera.hevc', 'driver': 'dcamera.hevc'}


def locate(enc: np.ndarray, t: float):
  """(segment, frame index within segment, frame time) of the frame nearest to log time t. enc rows: seg, idx, sof, eof."""
  i = int(np.argmin(np.abs(enc[:, 3] - t)))
  return int(enc[i, 0]), int(enc[i, 1]), float(enc[i, 3])


def grab(raw_dir: Path, route: str, camera: str, wanted: dict, size: tuple | None = None):
  """Decode once per segment. wanted: {segment: set(frame indices)} -> {(segment, idx): HxWx3 uint8 RGB}.
  size=(w, h) scales while decoding so long clips don't hold full-resolution frames in memory."""
  out = {}
  for seg, idxs in sorted(wanted.items()):
    last = max(idxs)
    with av.open(str(raw_dir / f'{route}--{seg}' / CAMERA_FILE[camera]), format='hevc') as c:
      for i, f in enumerate(c.decode(video=0)):
        if i in idxs:
          out[(seg, i)] = (f.reformat(width=size[0], height=size[1], format='rgb24') if size else f).to_ndarray(format='rgb24')
        if i >= last:
          break
  return out
