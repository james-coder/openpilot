"""Clip timeline and playback math for the Dashcam screen. Pure numpy, no I/O, no UI.

A Clip is the event's segment plus (when the event is near a boundary) its neighbours, laid end to end. Global time 0 is the first
frame of the first part. The PlaybackClock owns position, rate and play state; it never reads frames, so every rule (stepping at any
rate, clamping, end of clip, scrub mapping) is testable with plain numbers.
"""
from dataclasses import dataclass, field

import numpy as np

from openpilot.system.review.player.media import QlogSummary, VideoIndex

RATES = (0.1, 0.25, 0.5, 1.0, 2.0)
JUMP_S = 5.0
CONTEXT_S = 15.0         # include the neighbouring segment when the event is this close to the boundary
PRE_ROLL_S = 5.0         # open paused this long before the event
MAX_TICK_DT = 0.25       # a stalled UI frame must not make playback leap
TELEMETRY_STALE_S = 1.0


@dataclass
class Part:
  segment: str
  index: VideoIndex
  qlog: QlogSummary | None = None
  start: int = 0           # global index of this part's first frame
  t0: float = 0.0          # global time of this part's first frame
  _valid: np.ndarray | None = field(default=None, repr=False)

  @property
  def duration(self) -> float:
    return self.index.duration

  def _frames_with_log_time(self):
    if self._valid is None:
      fm = self.qlog.frame_mono[:self.index.count] if self.qlog is not None else np.empty(0)
      self._valid = np.flatnonzero(np.isfinite(fm))
    return self._valid

  def local_time(self, mono):
    """Video time (seconds from this part's first frame) of log monotonic time(s); scalar or array. None when the qlog gave no frame
    timestamps. Extrapolates linearly from the nearest stamped frame, so an event just before the first frame still maps."""
    valid = self._frames_with_log_time()
    if len(valid) == 0:
      return None
    fm = self.qlog.frame_mono[valid]
    m = np.asarray(mono, dtype=np.float64)
    if len(fm) > 1:
      j = np.clip(np.searchsorted(fm, m), 1, len(fm) - 1)
      k = np.where(np.abs(m - fm[j - 1]) <= np.abs(m - fm[j]), j - 1, j)
    else:
      k = np.zeros(m.shape, dtype=int)
    out = self.index.times[valid][k] + (m - fm[k])
    return float(out) if np.ndim(mono) == 0 else out


class Clip:
  def __init__(self, parts: list[Part], event_mono: float | None = None, main: int = 0):
    if not parts:
      raise ValueError('a clip needs at least one part')
    self.parts = parts
    n, t = 0, 0.0
    for p in parts:
      p.start, p.t0 = n, t
      n += p.index.count
      t += p.duration
    self.count = n
    self.duration = t
    self.times = np.concatenate([p.t0 + p.index.times for p in parts])
    self.size = (parts[main].index.width, parts[main].index.height)
    self.event_time = None
    if event_mono is not None:
      local = parts[main].local_time(event_mono)
      if local is not None:
        self.event_time = float(min(max(parts[main].t0 + local, 0.0), self.duration))
    self.telemetry = _telemetry(parts)

  def frame_at(self, t: float) -> int:
    """Index of the frame on screen at time t (the last frame that started at or before it)."""
    return int(min(max(np.searchsorted(self.times, t + 1e-9, side='right') - 1, 0), self.count - 1))

  def locate(self, g: int) -> tuple:
    """(part number, frame index within the part) of global frame g."""
    for i in range(len(self.parts) - 1, -1, -1):
      if g >= self.parts[i].start:
        return i, g - self.parts[i].start
    return 0, 0

  def start_position(self) -> float:
    """Where to open: PRE_ROLL_S before the event, or the start when its time is unknown."""
    if self.event_time is None:
      return 0.0
    return float(self.times[self.frame_at(max(0.0, self.event_time - PRE_ROLL_S))])


def needs_neighbours(local_time: float | None, duration: float) -> tuple:
  """(want previous, want next) segment for an event at local_time seconds into a segment of `duration` seconds."""
  if local_time is None:
    return False, False
  return local_time < CONTEXT_S, local_time > duration - CONTEXT_S


# ---- telemetry -----------------------------------------------------------------------------------------------------------------

@dataclass
class Telemetry:
  t: np.ndarray            # global clip time of each carState sample (increasing)
  speed: np.ndarray        # m/s
  brake: np.ndarray
  left: np.ndarray
  right: np.ndarray

  def at(self, t: float) -> dict | None:
    """Most recent sample at or before t, unless it is older than TELEMETRY_STALE_S."""
    if len(self.t) == 0:
      return None
    i = int(np.searchsorted(self.t, t + 1e-9, side='right')) - 1
    if i < 0 or t - self.t[i] > TELEMETRY_STALE_S:
      return None
    return dict(speed=float(self.speed[i]), brake=bool(self.brake[i]), left=bool(self.left[i]), right=bool(self.right[i]))

  def buckets(self, n: int, duration: float):
    """(speed per bucket with NaN where no sample, brake per bucket, blinker per bucket) for drawing a strip n pixels wide."""
    speed = np.full(n, np.nan)
    brake = np.zeros(n, dtype=bool)
    blink = np.zeros(n, dtype=bool)
    if len(self.t) == 0 or n <= 0 or duration <= 0:
      return speed, brake, blink
    b = np.clip((self.t / duration * n).astype(int), 0, n - 1)
    total = np.zeros(n)
    cnt = np.zeros(n)
    np.add.at(total, b, self.speed)
    np.add.at(cnt, b, 1)
    ok = cnt > 0
    speed[ok] = total[ok] / cnt[ok]
    np.bitwise_or.at(brake, b, self.brake)
    np.bitwise_or.at(blink, b, self.left | self.right)
    return speed, brake, blink


def _telemetry(parts: list[Part]) -> Telemetry | None:
  ts, sp, br, lf, rt = [], [], [], [], []
  for p in parts:
    q = p.qlog
    if q is None or len(q.car_t) == 0:
      continue
    local = p.local_time(q.car_t)
    if local is None:
      continue
    keep = (local >= -0.5) & (local <= p.duration + 0.5)
    ts.append(p.t0 + local[keep])
    sp.append(q.car_speed[keep])
    br.append(q.car_brake[keep])
    lf.append(q.car_left[keep])
    rt.append(q.car_right[keep])
  if not ts:
    return None
  t = np.concatenate(ts)
  order = np.argsort(t, kind='stable')
  return Telemetry(t[order], np.concatenate(sp)[order], np.concatenate(br)[order], np.concatenate(lf)[order], np.concatenate(rt)[order])


# ---- playback clock ------------------------------------------------------------------------------------------------------------

def snap_rate(rate: float) -> float:
  return min(RATES, key=lambda r: abs(r - rate))


class PlaybackClock:
  """Position in clip seconds. Playback holds the last frame at the end rather than running past it."""

  def __init__(self, times: np.ndarray, duration: float, position: float = 0.0):
    self.times = times
    self.duration = float(duration)
    self.last = float(times[-1])
    self.rate = 1.0
    self.playing = False
    self.ended = False
    self.stalled = False
    self.pos = min(max(float(position), 0.0), self.last)

  @property
  def frame(self) -> int:
    return int(min(max(np.searchsorted(self.times, self.pos + 1e-9, side='right') - 1, 0), len(self.times) - 1))

  def set_rate(self, rate: float) -> float:
    self.rate = snap_rate(rate)
    return self.rate

  def play(self) -> None:
    if self.pos >= self.last - 1e-9:
      self.pos = 0.0           # play at the end starts again
    self.ended = False
    self.playing = True

  def pause(self) -> None:
    self.playing = False
    self.stalled = False

  def toggle(self) -> None:
    self.pause() if self.playing else self.play()

  def seek(self, t: float) -> None:
    self.pos = min(max(float(t), 0.0), self.last)
    self.ended = False

  def seek_fraction(self, f: float) -> None:
    self.seek(min(max(float(f), 0.0), 1.0) * self.duration)

  def jump(self, seconds: float) -> None:
    self.seek(self.pos + seconds)

  def step(self, n: int) -> None:
    """Move whole frames, independent of rate; pauses so the exact frame stays up."""
    self.pause()
    self.ended = False
    self.pos = float(self.times[min(max(self.frame + int(n), 0), len(self.times) - 1)])

  def tick(self, dt: float, available=None) -> None:
    """Advance by dt wall seconds at the current rate. `available(frame)` False means the frame is not decoded yet: wait for it
    instead of racing ahead, so a slow decoder shows as buffering and never skips."""
    if not self.playing:
      return
    target = self.pos + min(max(dt, 0.0), MAX_TICK_DT) * self.rate
    if target >= self.last - 1e-9:
      self.pos, self.playing, self.ended, self.stalled = self.last, False, True, False
      return
    idx = int(min(max(np.searchsorted(self.times, target + 1e-9, side='right') - 1, 0), len(self.times) - 1))
    if available is not None and idx != self.frame and not available(idx):
      self.stalled = True
      return
    self.stalled = False
    self.pos = target


# ---- scrub bar -----------------------------------------------------------------------------------------------------------------

def scrub_fraction(x: float, bar_x: float, bar_w: float) -> float:
  return 0.0 if bar_w <= 0 else min(max((x - bar_x) / bar_w, 0.0), 1.0)


def scrub_x(t: float, duration: float, bar_x: float, bar_w: float) -> float:
  return bar_x + (0.0 if duration <= 0 else min(max(t / duration, 0.0), 1.0)) * bar_w


def format_clock(seconds: float) -> str:
  s = max(0.0, float(seconds))
  return f'{int(s // 60)}:{s % 60:04.1f}'
