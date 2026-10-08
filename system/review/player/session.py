"""The Dashcam screen's brain: event list, selected clip, playback state and a background decode worker. No UI imports.

Threads. The UI thread calls update() once per frame and reads view(); both are cheap and never block, wait on a lock, or touch the
disk. Everything slow (reading events.jsonl, checking segments, indexing and decoding video, reading the qlog, setting the preserve
flag) runs on one worker thread that drops real-time scheduling first. The worker publishes results by replacing whole objects
(a single attribute assignment), and the UI thread publishes requests the same way, so neither side holds a lock. Decoded frames
live in a byte-bounded FrameCache.

Failure. A bad file becomes a message, never an exception: every worker step is guarded, a damaged stretch of video is recorded as a gap
and skipped, a stalled decoder pauses playback, and a dead worker shows "playback unavailable". Nothing here is needed for driving.
"""
import os
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from openpilot.system.review.player.cache import DEFAULT_MAX_BYTES, FrameCache
from openpilot.system.review.player.events import (
  PRESERVE_ATTR, EventRow, SegmentStatus, events_path_for, neighbour_segment, read_events, segment_status, valid_segment_name,
)
from openpilot.system.review.player.media import MediaError, index_video, read_qlog, read_video, to_rgb
from openpilot.system.review.player.timeline import Clip, Part, PlaybackClock, Telemetry, needs_neighbours

AHEAD = 72              # frames kept ready past the playhead (3.6 s at 20 fps)
FILL_BEHIND = 12        # frames kept behind it for single-stepping back
FORWARD_SKIP = 60       # decode through up to this many unwanted frames rather than restarting at a keyframe
DECODE_BUDGET_S = 0.012  # one slice of decoding before the worker re-checks for new requests
STALL_TIMEOUT_S = 6.0   # playback waiting this long for a frame pauses itself
NOTICE_S = 4.0
MAX_GAPS = 48
WORKER_FAILURE_LIMIT = 40


def default_logger(message: str) -> None:
  try:
    from openpilot.common.swaglog import cloudlog
    cloudlog.exception(message)
  except Exception:
    pass


def default_drop_realtime() -> None:
  from openpilot.common.realtime import drop_realtime
  drop_realtime()


def default_protect(path: str) -> None:
  from openpilot.system.loggerd.xattr_cache import setxattr
  setxattr(path, PRESERVE_ATTR, b'1')


@dataclass
class EventsSnapshot:
  state: str = 'loading'            # loading | ok | missing | unreadable
  rows: tuple = ()
  statuses: dict = field(default_factory=dict)   # row.key -> SegmentStatus


@dataclass
class ClipState:
  gen: int
  row: EventRow
  clip: Clip | None = None
  error: str | None = None
  cache: FrameCache | None = None
  gaps: tuple = ()                  # ((lo, hi), ...) frames that cannot be produced; replaced, never mutated
  damaged: str | None = None

  def is_gap(self, g: int) -> bool:
    return any(lo <= g < hi for lo, hi in self.gaps)


@dataclass
class View:
  events_state: str
  rows: tuple
  statuses: dict
  selected: str | None
  clip_state: str                   # none | loading | error | ready
  message: str = ''
  position: float = 0.0
  duration: float = 0.0
  event_time: float | None = None
  playing: bool = False
  rate: float = 1.0
  ended: bool = False
  buffering: bool = False
  hot: bool = False
  frame: np.ndarray | None = None
  frame_id: tuple | None = None
  frame_size: tuple = (526, 330)
  in_gap: bool = False
  telemetry: Telemetry | None = None
  now: dict | None = None
  can_protect: bool = False
  segment_status: SegmentStatus | None = None
  notice: str = ''
  worker_alive: bool = True


class _Cursor:
  def __init__(self, gen: int, part_i: int, part: Part, first: int):
    self.gen, self.part_i, self.part = gen, part_i, part
    self.it = read_video(part.index, first)
    self.next = part.start + part.index.start_frame_for(first)   # lowest global frame the iterator can yield next
    self.pending = None

  def close(self) -> None:
    try:
      self.it.close()
    except Exception:
      pass


class DashcamSession:
  def __init__(self, log_root, events_path=None, *, max_cache_bytes: int = DEFAULT_MAX_BYTES, protect_fn=None, logger=None,
               drop_realtime_fn=None, getxattr_fn=None, monotonic=time.monotonic):
    self._root = str(log_root)
    self._events_path = events_path or events_path_for(log_root)
    self._max_cache_bytes = max_cache_bytes
    self._protect_fn = protect_fn or default_protect
    self._log = logger or default_logger
    self._drop_realtime = drop_realtime_fn or default_drop_realtime
    self._getxattr = getxattr_fn
    self._now = monotonic
    # ---- UI-thread state
    self._clock: PlaybackClock | None = None
    self._clock_gen = -1
    self._selected: EventRow | None = None
    self._resume_after_scrub = False
    self._scrubbing = False
    self._notice = ('', 0.0)
    self._stall_s = 0.0
    self._hot = False
    self._shown: tuple | None = None        # (gen, idx, frame) last frame handed to the screen
    self._seen_protect = 0
    self._protect_seq = 0
    self._gen = 0
    # ---- published by the UI thread, read by the worker (each is replaced whole)
    self._want = (0, 0)                     # (clip generation, frame the playhead is on)
    self._want_clip: tuple | None = None    # (generation, row)
    self._want_events = False
    self._want_protect: tuple | None = None
    # ---- published by the worker, read by the UI thread
    self._events = EventsSnapshot()
    self._clip: ClipState | None = None
    self._protect_result: tuple = (0, True, '')
    self._worker_dead: str | None = None
    # ---- worker-only
    self._cursor: _Cursor | None = None
    self._blocked: tuple | None = None
    self._failures = 0
    self.stats = dict(decoded=0, restarts=0, gaps=0, worker_errors=0, worker_thread=None)
    self._stop = threading.Event()
    self._wake = threading.Event()
    self._thread: threading.Thread | None = None
    self._lock = threading.Lock()           # only guards start()/close() bookkeeping, never held by update()/view()

  # ---- lifecycle ----------------------------------------------------------------------------------------------------------------

  def start(self) -> None:
    with self._lock:
      if self._thread is None and not self._stop.is_set():
        self._want_events = True
        self._thread = threading.Thread(target=self._run, name='dashcam-decode', daemon=True)
        self._thread.start()

  def close(self) -> None:
    """Stop decoding. Does not wait for the worker (it exits within one decode slice); safe to call twice."""
    self._stop.set()
    self._wake.set()
    self._clip = None
    self._clock = None

  def join(self, timeout: float = 2.0) -> bool:
    t = self._thread
    if t is not None:
      t.join(timeout)
      return not t.is_alive()
    return True

  @property
  def worker_alive(self) -> bool:
    """False only if the worker died on its own; a deliberate close() is not a failure."""
    t = self._thread
    return self._worker_dead is None and (t is None or t.is_alive() or self._stop.is_set())

  # ---- UI-thread API -------------------------------------------------------------------------------------------------------------

  def refresh(self) -> None:
    self._want_events = True
    self._wake.set()

  def select(self, key: str | None) -> None:
    row = next((r for r in self._events.rows if r.key == key), None)
    old = self._clip
    if old is not None and old.cache is not None:
      old.cache.clear()   # retired: free its frames now rather than when the next clip finishes loading
    self._gen += 1
    self._selected = row
    self._clock = None
    self._clock_gen = -1
    self._shown = None
    self._stall_s = 0.0
    self._scrubbing = False
    self._want = (self._gen, 0)
    self._want_clip = (self._gen, row) if row is not None else None
    self._wake.set()

  def _need_clock(self) -> PlaybackClock | None:
    return self._clock if self._clock_gen == self._gen else None

  def toggle_play(self) -> None:
    c = self._need_clock()
    if c is not None and not self._hot:
      c.toggle()

  def step(self, n: int) -> None:
    c = self._need_clock()
    if c is not None:
      c.step(n)

  def jump(self, seconds: float) -> None:
    c = self._need_clock()
    if c is not None:
      c.jump(seconds)

  def set_rate(self, rate: float) -> None:
    c = self._need_clock()
    if c is not None:
      c.set_rate(rate)

  def scrub(self, fraction: float, phase: str) -> None:
    """phase: 'start' (finger down), 'move', 'end', or 'cancel' (touch lost: no seek).
    Playback is held while the finger is down and resumes if it was running."""
    c = self._need_clock()
    if c is None:
      return
    if phase == 'start':
      self._resume_after_scrub = c.playing
      self._scrubbing = True
      c.pause()
    if self._scrubbing and phase != 'cancel':
      c.seek_fraction(fraction)
    if phase in ('end', 'cancel') and self._scrubbing:
      self._scrubbing = False
      if self._resume_after_scrub and not self._hot:
        c.play()

  def protect(self) -> None:
    row = self._selected
    if row is None or row.segment is None:
      return
    status = self._events.statuses.get(row.key)
    if status is not None and status.state == 'deleted':
      self._notice = ('Footage was already deleted', self._now() + NOTICE_S)
      return
    self._protect_seq += 1
    self._want_protect = (self._protect_seq, row.segment)
    self._notice = ('Protecting...', self._now() + NOTICE_S)
    self._wake.set()

  def update(self, dt: float, hot: bool = False) -> None:
    """Once per rendered frame. Advances the playhead and tells the worker which frame is needed."""
    self._hot = hot
    state = self._clip
    if state is not None and state.gen == self._gen and state.clip is not None and self._clock_gen != self._gen:
      self._clock = PlaybackClock(state.clip.times, state.clip.duration, state.clip.start_position())
      self._clock_gen = self._gen
    result = self._protect_result
    if result[0] != self._seen_protect:
      self._seen_protect = result[0]
      self._notice = (result[2], self._now() + NOTICE_S)
    c = self._need_clock()
    if c is None or state is None or state.cache is None:
      return
    if hot and c.playing:
      c.pause()
      self._notice = ('Device is hot: playback paused', self._now() + NOTICE_S)
    cache = state.cache
    c.tick(dt, lambda i: cache.has(i) or state.is_gap(i))
    if c.stalled:
      self._stall_s += min(max(dt, 0.0), 0.25)
      if self._stall_s > STALL_TIMEOUT_S:
        c.pause()
        self._stall_s = 0.0
        self._notice = ('Video decoder is not responding: paused', self._now() + NOTICE_S)
    else:
      self._stall_s = 0.0
    want = (self._gen, c.frame)
    if want != self._want:
      self._want = want
      self._wake.set()

  def view(self) -> View:
    events = self._events
    row = self._selected
    now = self._now()
    notice = self._notice[0] if now < self._notice[1] else ''
    status = events.statuses.get(row.key) if row is not None else None
    v = View(events.state, events.rows, events.statuses, row.key if row else None, 'none', notice=notice, hot=self._hot,
             segment_status=status, worker_alive=self.worker_alive,
             can_protect=row is not None and row.segment is not None and status is not None and status.state != 'deleted')
    if not v.worker_alive:
      v.clip_state, v.message = 'error', self._worker_dead or 'Playback is unavailable.'
      return v
    if row is None:
      return v
    state = self._clip
    c = self._need_clock()
    if state is None or state.gen != self._gen:
      v.clip_state = 'loading'
      return v
    if state.error is not None or state.clip is None:
      v.clip_state, v.message = 'error', state.error or 'Video unavailable.'
      return v
    if c is None or state.cache is None:
      v.clip_state = 'loading'
      return v
    clip = state.clip
    idx = c.frame
    frame = state.cache.get(idx)
    v.in_gap = frame is None and state.is_gap(idx)
    if frame is not None:
      self._shown = (self._gen, idx, frame)
    shown = self._shown
    v.clip_state = 'ready'
    v.position, v.duration, v.event_time = c.pos, c.duration, clip.event_time
    v.playing, v.rate, v.ended = c.playing, c.rate, c.ended
    v.buffering = frame is None and not v.in_gap
    v.frame, v.frame_id = (shown[2], (shown[0], shown[1])) if shown is not None else (None, None)
    v.frame_size = clip.size
    v.telemetry = clip.telemetry
    v.now = clip.telemetry.at(c.pos) if clip.telemetry is not None else None
    v.message = state.damaged or ''
    return v

  # ---- worker --------------------------------------------------------------------------------------------------------------------

  def _run(self) -> None:
    self.stats['worker_thread'] = threading.get_ident()
    try:
      self._drop_realtime()   # a thread started by the SCHED_FIFO UI process inherits FIFO; this must not compete with driving
    except Exception:
      self._log('dashcam: could not drop real-time scheduling for the decode worker')
    try:
      os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)   # Linux nice is per thread: yield to the UI and driving processes
    except Exception:
      pass
    try:
      while not self._stop.is_set():
        did = False
        try:
          did = self._work_once()
          self._failures = 0
        except Exception:
          self._failures += 1
          self.stats['worker_errors'] += 1
          if self._failures in (1, 10):
            self._log('dashcam: decode worker error')
          if self._failures >= WORKER_FAILURE_LIMIT:
            self._worker_dead = 'Playback stopped after repeated errors.'
            return
          self._close_cursor()
          self._stop.wait(min(0.05 * self._failures, 1.0))
        if not did:
          self._wake.wait(0.05)
          self._wake.clear()
    finally:
      self._close_cursor()

  def _work_once(self) -> bool:
    """One pass over pending requests, then one slice of decoding. Returns True if anything was done. A failing request becomes a
    visible error state (never a silent hang); only a failure of the decode loop itself counts toward declaring the worker dead."""
    did = False
    if self._want_events:
      self._want_events = False
      self._guarded('event list', self._load_events, lambda: setattr(self, '_events', EventsSnapshot('unreadable')))
      did = True
    req, self._want_clip = self._want_clip, None
    if req is not None:
      gen, row = req

      def failed():
        if gen == self._gen:
          self._clip = ClipState(gen, row, error='Video unavailable (unexpected error).')
      self._guarded('clip load', lambda: self._load_clip(gen, row), failed)
      did = True
    prot, self._want_protect = self._want_protect, None
    if prot is not None:
      self._guarded('protect', lambda: self._do_protect(*prot), lambda: setattr(self, '_protect_result', (prot[0], False, 'Could not protect this footage')))
      did = True
    return self._decode_slice() or did

  def _guarded(self, what: str, fn, on_error) -> None:
    try:
      fn()
    except Exception:
      self.stats['worker_errors'] += 1
      self._log(f'dashcam: {what} failed')
      try:
        on_error()
      except Exception:
        pass

  def _load_events(self) -> None:
    rows, state = read_events(self._events_path)
    statuses: dict[str, SegmentStatus] = {}
    by_segment: dict = {}
    for r in rows:
      if r.segment not in by_segment:
        by_segment[r.segment] = segment_status(self._root, r.segment, self._getxattr)
      statuses[r.key] = by_segment[r.segment]
    self._events = EventsSnapshot(state, tuple(rows), statuses)

  def _load_part(self, segment: str) -> Part:
    base = os.path.join(self._root, segment)
    index = index_video(os.path.join(base, 'qcamera.ts'))
    try:
      qlog = read_qlog(os.path.join(base, 'qlog.zst'))
    except MediaError:
      qlog = None
    return Part(segment, index, qlog)

  def _load_clip(self, gen: int, row: EventRow) -> None:
    def publish(**kw):
      if gen == self._gen:   # a newer selection supersedes this one
        self._clip = ClipState(gen, row, **kw)

    if row.segment is None or not valid_segment_name(row.segment):
      return publish(error='No segment was recorded for this event.')
    status = segment_status(self._root, row.segment, self._getxattr)
    if status.state == 'deleted':
      return publish(error='This footage has been deleted.')
    if status.state == 'recording':
      return publish(error='This segment is still being recorded.')
    if status.state != 'ready':
      return publish(error='No video was saved for this segment.')
    try:
      main = self._load_part(row.segment)
    except MediaError as e:
      return publish(error=f'Video unavailable: {e}.')
    local = main.local_time(row.mono) if row.mono is not None and main.qlog is not None else None
    prev_wanted, next_wanted = needs_neighbours(local, main.duration)
    parts, main_at = [], 0
    for delta, wanted in ((-1, prev_wanted), (0, True), (1, next_wanted)):
      if not wanted:
        continue
      if delta == 0:
        main_at = len(parts)
        parts.append(main)
        continue
      name = neighbour_segment(row.segment, delta)
      if name is None or segment_status(self._root, name, self._getxattr).state != 'ready':
        continue
      try:
        parts.append(self._load_part(name))
      except MediaError:
        continue   # context is a bonus; the event's own segment is enough
    if gen != self._gen:
      return None
    clip = Clip(parts, row.mono, main_at)
    cache = FrameCache(self._max_cache_bytes)
    self._close_cursor()
    self._blocked = None
    return publish(clip=clip, cache=cache)

  def _do_protect(self, seq: int, segment: str) -> None:
    path = os.path.join(self._root, segment)
    reason = ''
    try:
      if not valid_segment_name(segment):
        raise ValueError('bad segment name')
      self._protect_fn(path)
    except Exception as e:
      self._log(f'dashcam: could not protect {segment}')
      reason = type(e).__name__
    status = segment_status(self._root, segment, self._getxattr)
    ok = not reason and status.protected is True   # read it back: the flag, not the call, is what the deleter honours
    events = self._events
    statuses = dict(events.statuses)
    for r in events.rows:
      if r.segment == segment:
        statuses[r.key] = status
    self._events = EventsSnapshot(events.state, events.rows, statuses)
    text = 'Protected: the deleter will keep this footage' if ok else 'Could not protect this footage' + (f' ({reason})' if reason else '')
    self._protect_result = (seq, ok, text)

  # ---- decoding ------------------------------------------------------------------------------------------------------------------

  def _close_cursor(self) -> None:
    c, self._cursor = self._cursor, None
    if c is not None:
      c.close()

  def _add_gap(self, state: ClipState, lo: int, hi: int) -> None:
    if hi <= lo:
      return
    gaps = sorted((*state.gaps, (lo, hi)))
    merged = [gaps[0]]
    for a, b in gaps[1:]:
      if a <= merged[-1][1]:
        merged[-1] = (merged[-1][0], max(merged[-1][1], b))
      else:
        merged.append((a, b))
    if len(merged) > MAX_GAPS:
      merged = [(merged[0][0], merged[-1][1])]
    state.gaps = tuple(merged)
    self.stats['gaps'] += 1

  def _first_missing(self, state: ClipState, anchor: int, lo: int, hi: int) -> int | None:
    cache = state.cache
    for g in (anchor, *range(anchor + 1, hi), *range(lo, anchor)):
      if not cache.has(g) and not state.is_gap(g):
        return g
    return None

  def _decode_slice(self) -> bool:
    state = self._clip
    gen, anchor = self._want
    if state is None or state.clip is None or state.cache is None or state.gen != gen or self._hot:
      if self._cursor is not None and (state is None or self._cursor.gen != gen):
        self._close_cursor()
      return False
    clip, cache = state.clip, state.cache
    anchor = min(max(anchor, 0), clip.count - 1)
    if self._blocked == (gen, anchor):
      return False
    lo, hi = max(0, anchor - FILL_BEHIND), min(clip.count, anchor + AHEAD)
    m = self._first_missing(state, anchor, lo, hi)
    if m is None:
      return False
    part_i, local = clip.locate(m)
    cur = self._cursor
    if cur is None or cur.gen != gen or cur.part_i != part_i or m < cur.next or m - cur.next > FORWARD_SKIP:
      self._close_cursor()
      cur = self._cursor = _Cursor(gen, part_i, clip.parts[part_i], local)
      self.stats['restarts'] += 1
    part = cur.part
    part_end = part.start + part.index.count
    deadline = self._now() + DECODE_BUDGET_S
    worked = False
    while self._now() < deadline and not self._stop.is_set() and not self._hot:
      g_now, anchor = self._want
      if g_now != gen:
        break
      anchor = min(max(anchor, 0), clip.count - 1)
      lo, hi = max(0, anchor - FILL_BEHIND), min(clip.count, anchor + AHEAD)
      if cur.pending is None:
        try:
          local_idx, frame = next(cur.it)
        except StopIteration:
          self._add_gap(state, cur.next, part_end)
          state.damaged = state.damaged or ('Video ends early here' if cur.next < part_end else None)
          self._close_cursor()
          return True
        except MediaError as e:
          self._add_gap(state, cur.next, part_end)
          state.damaged = f'{e}'
          self._close_cursor()
          return True
        g = part.start + local_idx
        if g > cur.next:
          self._add_gap(state, cur.next, g)   # the decoder skipped frames: they will never appear
        cur.pending = (g, frame)
      g, frame = cur.pending
      if g >= hi:
        return worked
      if g >= lo and not cache.has(g):
        if not cache.put(g, to_rgb(frame), anchor):
          self._blocked = (gen, anchor)
          return worked
        self.stats['decoded'] += 1
        worked = True
      cur.pending = None
      cur.next = g + 1
    return True
