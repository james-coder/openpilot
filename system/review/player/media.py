"""Reading a segment's low-resolution video (qcamera.ts) and the few qlog messages the player needs. Read-only, bounded, no UI.

qcamera.ts is a small H.264 MPEG-TS stream (about 2 MB a minute). It is indexed once by demuxing (no decoding, milliseconds), then
decoded sequentially from the keyframe at or before the wanted frame. There is no byte/time seeking, so a seek can never land
after its target. Every failure surfaces as MediaError so callers can show a message and carry on.
"""
import os
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

MAX_VIDEO_BYTES = 64 << 20     # a one-minute qcamera is ~2 MB; anything this large is not one
MAX_FRAMES = 6000              # 5 minutes at 20 fps
MAX_QLOG_RAW_BYTES = 192 << 20
MAX_QLOG_EVENTS = 400_000
MAX_DECODE_ERRORS = 20
DEFAULT_SIZE = (526, 330)


class MediaError(Exception):
  """The file is missing, empty, corrupt, unreadable or implausibly large. The message is short and fit to show a person."""


@dataclass(frozen=True)
class VideoIndex:
  path: str
  times: np.ndarray             # seconds from the first frame, presentation order, strictly increasing
  frame_dur: float
  width: int
  height: int
  packet_pts: tuple             # raw pts of each packet in decode order
  packet_key: tuple             # is_keyframe, same order
  pts_to_frame: dict            # raw pts -> presentation index

  @property
  def count(self) -> int:
    return len(self.times)

  @property
  def duration(self) -> float:
    return float(self.times[-1]) + self.frame_dur

  def start_packet_for(self, frame: int) -> int:
    """Decode-order packet number of the last keyframe whose own frame index is <= frame."""
    best = 0
    for n, (pts, key) in enumerate(zip(self.packet_pts, self.packet_key, strict=True)):
      if key and self.pts_to_frame.get(pts, 1 << 30) <= frame:
        best = n
    return best

  def start_frame_for(self, frame: int) -> int:
    """Index of the first frame read_video() yields when asked to start at `frame` (its keyframe)."""
    return self.pts_to_frame.get(self.packet_pts[self.start_packet_for(frame)], 0)


def _open(path):
  import av
  return av.open(path, mode='r', timeout=5.0)


def index_video(path) -> VideoIndex:
  path = str(path)
  try:
    size = os.stat(path).st_size
  except OSError as e:
    raise MediaError('video file is missing or unreadable') from e
  if size <= 0:
    raise MediaError('video file is empty')
  if size > MAX_VIDEO_BYTES:
    raise MediaError('video file is too large')
  pts, keys = [], []
  width = height = 0
  tb = None
  try:
    with _open(path) as c:
      if not c.streams.video:
        raise MediaError('no video stream in file')
      vs = c.streams.video[0]
      tb = vs.time_base
      width, height = int(vs.codec_context.width or 0), int(vs.codec_context.height or 0)
      for p in c.demux(vs):
        if p.size == 0:
          continue
        t = p.pts if p.pts is not None else p.dts
        pts.append(t)
        keys.append(bool(p.is_keyframe))
        if len(pts) > MAX_FRAMES:
          raise MediaError('video has too many frames')
  except MediaError:
    raise
  except Exception as e:  # av.error.FFmpegError and friends: corrupt or truncated container
    raise MediaError(f'video cannot be read ({type(e).__name__})') from e
  if not pts:
    raise MediaError('video has no frames')
  if any(t is None for t in pts):
    raise MediaError('video has no timestamps')
  ordered = sorted(set(pts))
  base = ordered[0]
  times = np.array([float((t - base) * tb) for t in ordered], dtype=np.float64)
  frame_dur = float(np.median(np.diff(times))) if len(times) > 1 else 0.05
  if not frame_dur > 0:
    raise MediaError('video timestamps are invalid')
  return VideoIndex(path, times, frame_dur, width or DEFAULT_SIZE[0], height or DEFAULT_SIZE[1], tuple(pts), tuple(keys),
                    {t: i for i, t in enumerate(ordered)})


def read_video(index: VideoIndex, first: int) -> Iterator[tuple]:
  """Yield (frame index, av.VideoFrame) in presentation order starting at the keyframe at or before `first`; the caller drops the
  lead-in frames and converts with to_rgb(). Ends at the end of the stream. Raises MediaError after repeated decode errors.
  Close the generator to release the file."""
  start = index.start_packet_for(max(0, first))
  expected = None
  errors = 0
  try:
    with _open(index.path) as c:
      vs = c.streams.video[0]
      vs.codec_context.thread_type = 'SLICE'
      vs.codec_context.thread_count = 2
      n = -1
      for p in c.demux(vs):
        n += 1
        if n < start:
          continue
        try:
          frames = vs.codec_context.decode(p if p.size else None)
          errors = 0
        except Exception as e:
          errors += 1
          if errors > MAX_DECODE_ERRORS:
            raise MediaError(f'video is damaged ({type(e).__name__})') from e
          continue
        for fr in frames:
          idx = index.pts_to_frame.get(fr.pts)
          if idx is None:
            idx = expected if expected is not None else index.pts_to_frame.get(index.packet_pts[start], 0)
          expected = idx + 1
          yield idx, fr
  except MediaError:
    raise
  except Exception as e:
    raise MediaError(f'video cannot be read ({type(e).__name__})') from e


def to_rgb(frame) -> np.ndarray:
  """HxWx3 uint8, C-contiguous."""
  return np.ascontiguousarray(frame.to_ndarray(format='rgb24'))


# ---- qlog ----------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class QlogSummary:
  frame_mono: np.ndarray        # monotonic seconds of each qcamera frame (index = frame number), NaN where unknown
  car_t: np.ndarray             # carState (monotonic seconds), ~10 Hz in qlog
  car_speed: np.ndarray         # m/s
  car_brake: np.ndarray         # bool
  car_left: np.ndarray          # bool
  car_right: np.ndarray         # bool


def read_qlog(path, max_seconds: float = 3.0, monotonic=None) -> QlogSummary:
  """qRoadEncodeIdx (frame number -> log time) and carState from one segment's qlog.zst, bounded in size, events and time."""
  import time

  import zstandard
  from cereal import log
  clock = monotonic or time.monotonic
  deadline = clock() + max_seconds
  path = str(path)
  try:
    size = os.stat(path).st_size
  except OSError as e:
    raise MediaError('qlog is missing or unreadable') from e
  if size <= 0:
    raise MediaError('qlog is empty')
  chunks, total = [], 0
  try:
    with open(path, 'rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as s:
      while True:
        chunk = s.read(4 << 20)
        if not chunk:
          break
        total += len(chunk)
        if total > MAX_QLOG_RAW_BYTES:
          raise MediaError('qlog is too large')
        chunks.append(chunk)
  except MediaError:
    raise
  except Exception as e:  # zstd frame errors on a truncated or corrupt file
    if not chunks:
      raise MediaError(f'qlog cannot be read ({type(e).__name__})') from e
  raw = b''.join(chunks)
  frames: dict[int, float] = {}
  car = []
  count = 0
  it = log.Event.read_multiple_bytes(raw)
  while True:
    try:
      ev = next(it)
    except StopIteration:
      break
    except Exception:  # truncated final message
      break
    count += 1
    if count > MAX_QLOG_EVENTS or (count % 512 == 0 and clock() > deadline):
      raise MediaError('qlog took too long to read')
    kind = ev.which()
    if kind == 'qRoadEncodeIdx':
      e = ev.qRoadEncodeIdx
      if 0 <= e.segmentId < MAX_FRAMES:
        frames[int(e.segmentId)] = e.timestampEof / 1e9
    elif kind == 'carState':
      c = ev.carState
      car.append((ev.logMonoTime / 1e9, c.vEgo, c.brakePressed, c.leftBlinker, c.rightBlinker))
  mono = np.full(max(frames) + 1 if frames else 0, np.nan)
  for i, t in frames.items():
    mono[i] = t
  a = np.array(car, dtype=np.float64).reshape(-1, 5)
  order = np.argsort(a[:, 0], kind='stable')
  a = a[order]
  return QlogSummary(mono, a[:, 0], a[:, 1], a[:, 2] > 0.5, a[:, 3] > 0.5, a[:, 4] > 0.5)
