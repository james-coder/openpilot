"""Event list model for the Dashcam screen: parse eventd's events.jsonl and report the state of each event's footage.

Everything here tolerates a missing, empty, truncated or corrupt file and never raises into the caller. Records are written by
system/review/eventd.py: one line per start/update/end of an episode, {v, id, phase, mono, wall, kinds, peaks, segment}.
"""
import json
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_EVENTS = 200
TAIL_BYTES = 1 << 20          # only the newest ~1 MiB of the file is read (several thousand records)
MAX_LINE_BYTES = 64 * 1024    # longer lines are garbage
MAX_KINDS = 8
MAX_PEAKS = 8
PRESERVE_ATTR = 'user.preserve'
PLAUSIBLE_AFTER = datetime(2025, 1, 1).timestamp()  # a device clock before this was never set (or is badly wrong)
SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*--[0-9]+$')
PHASES = ('start', 'update', 'end')

KIND_LABELS = {'abs_stop': 'ABS stop', 'hard_brake': 'Hard brake', 'extreme_g': 'Extreme g', 'impact': 'Possible impact'}
PEAK_LABELS = {'decel_g': 'decel', 'horizontal_g': 'horizontal', 'jolt_h_g': 'jolt'}


def _number(value) -> float | None:
  if isinstance(value, bool) or not isinstance(value, int | float):
    return None
  value = float(value)
  return value if math.isfinite(value) else None


def valid_segment_name(name) -> bool:
  """A plain directory name <route>--<n>. Rejects anything that could escape the log root."""
  return isinstance(name, str) and len(name) < 200 and bool(SEGMENT_RE.match(name)) and '..' not in name.replace('--', '')


def split_segment(name: str) -> tuple[str, int] | None:
  if not valid_segment_name(name):
    return None
  route, _, num = name.rpartition('--')
  return route, int(num)


def neighbour_segment(name: str, delta: int) -> str | None:
  parts = split_segment(name)
  if parts is None or parts[1] + delta < 0:
    return None
  return f'{parts[0]}--{parts[1] + delta}'


@dataclass
class EventRow:
  key: str                      # unique within one parse and stable across reloads: id and trigger time
  id: int | None
  mono: float | None            # log monotonic clock (seconds) of the first trigger
  wall: float | None            # device wall clock when the record was written, exactly as stored (may be wrong)
  kinds: tuple = ()
  peaks: dict = field(default_factory=dict)
  segment: str | None = None    # segment being written at the first trigger
  segments: tuple = ()          # every segment named by the episode's records, in order
  complete: bool = False        # the closing 'end' record was seen


def parse_events(lines: Iterable, max_events: int = MAX_EVENTS) -> list[EventRow]:
  """Group records into episodes. Newest first, in file order (the file is append-only, so file order is truer than a possibly wrong
  wall clock). Lines that are not JSON objects with a known phase are skipped."""
  episodes: list[EventRow] = []
  current: EventRow | None = None
  for number, raw in enumerate(lines):
    try:
      if isinstance(raw, bytes):
        if len(raw) > MAX_LINE_BYTES:
          continue
        raw = raw.decode('utf-8', 'replace')
      elif len(raw) > MAX_LINE_BYTES:
        continue
      raw = raw.strip()
      if not raw:
        continue
      rec = json.loads(raw)
    except (ValueError, RecursionError, TypeError):
      continue
    if not isinstance(rec, dict) or rec.get('phase') not in PHASES:
      continue
    ident = rec.get('id') if isinstance(rec.get('id'), int) and not isinstance(rec.get('id'), bool) else None
    kinds = tuple(k[:32] for k in rec.get('kinds', ()) if isinstance(k, str))[:MAX_KINDS] if isinstance(rec.get('kinds'), list) else ()
    peaks = {}
    if isinstance(rec.get('peaks'), dict):
      for k, v in list(rec['peaks'].items())[:MAX_PEAKS]:
        n = _number(v)
        if isinstance(k, str) and n is not None:
          peaks[k[:32]] = n
    segment = rec.get('segment') if valid_segment_name(rec.get('segment')) else None
    if rec['phase'] == 'start' or current is None or current.id != ident:
      current = EventRow(key=str(number), id=ident, mono=_number(rec.get('mono')), wall=_number(rec.get('wall')),
                         segment=segment)
      episodes.append(current)
    if current.mono is None:
      current.mono = _number(rec.get('mono'))
    if current.wall is None:
      current.wall = _number(rec.get('wall'))
    current.kinds = tuple(dict.fromkeys(current.kinds + kinds))[:MAX_KINDS]
    for k, v in peaks.items():
      current.peaks[k] = max(current.peaks.get(k, v), v)
    if segment and segment not in current.segments:
      current.segments += (segment,)
    if current.segment is None:
      current.segment = segment
    if rec['phase'] == 'end':
      current.complete = True
  episodes.reverse()
  seen: dict[str, int] = {}
  for row in episodes:  # stable across reloads even when the file grows: id and trigger time, not a line number
    base = f'{row.id}@{row.mono:.3f}' if row.mono is not None else f'{row.id}@L{row.key}'
    seen[base] = seen.get(base, 0) + 1
    row.key = base if seen[base] == 1 else f'{base}#{seen[base]}'
  return episodes[:max_events]


def read_events(path, tail_bytes: int = TAIL_BYTES, max_events: int = MAX_EVENTS) -> tuple[list[EventRow], str]:
  """(rows, state). state: 'ok' (rows may be empty), 'missing' (no file yet) or 'unreadable'. Never raises."""
  try:
    with open(path, 'rb') as f:
      size = os.fstat(f.fileno()).st_size
      start = max(0, size - tail_bytes)
      f.seek(start)
      data = f.read(tail_bytes + 1)
  except FileNotFoundError:
    return [], 'missing'
  except (OSError, ValueError):
    return [], 'unreadable'
  lines = data.split(b'\n')
  if start > 0 and lines:
    lines = lines[1:]  # the first line is cut mid-record
  return parse_events(lines, max_events), 'ok'


# ---- presentation --------------------------------------------------------------------------------------------------------------

def describe_time(wall, now: float | None = None, tz=None) -> str:
  """The stored device time as given. Flags a clock that was probably never set or is ahead of now; never invents a time."""
  wall = _number(wall)
  if wall is None or wall <= 0:
    return 'Time unknown'
  try:
    text = datetime.fromtimestamp(wall, tz).strftime('%Y-%m-%d %H:%M:%S')
  except (OverflowError, OSError, ValueError):
    return 'Time unknown'
  if wall < PLAUSIBLE_AFTER:
    return text + ' (clock unset?)'
  if now is not None and wall > now + 86400:
    return text + ' (clock ahead?)'
  return text


def kinds_label(kinds) -> str:
  if not kinds:
    return 'Event'
  return ', '.join(KIND_LABELS.get(k, k.replace('_', ' ').capitalize()) for k in kinds)


def peaks_label(peaks: dict) -> str:
  parts = []
  for key in sorted(peaks):
    value = peaks[key]
    label = PEAK_LABELS.get(key, key.removesuffix('_g').replace('_', ' '))
    parts.append(f'{label} {value:.2f} g' if key.endswith('_g') else f'{label} {value:.2f}')
  return '  '.join(parts)


@dataclass(frozen=True)
class SegmentStatus:
  state: str                    # ready | recording | deleted | no_video | no_segment
  protected: bool | None = None  # user.preserve set; None when it could not be read


def segment_status(root, name, getxattr_fn=None) -> SegmentStatus:
  """Does this event's footage still exist, is it being written, is it flagged for the deleter? Never raises."""
  if name is None or not valid_segment_name(name):
    return SegmentStatus('no_segment')
  path = os.path.join(str(root), name)
  try:
    with os.scandir(path) as entries:
      names = {e.name for e in entries}
  except FileNotFoundError:
    return SegmentStatus('deleted', False)
  except OSError:
    return SegmentStatus('no_video')
  protected = None
  try:
    if getxattr_fn is None:
      from openpilot.system.loggerd.xattr_cache import getxattr_uncached as getxattr_fn
    protected = getxattr_fn(path, PRESERVE_ATTR) == b'1'
  except Exception:
    protected = None
  if any(n.endswith('.lock') for n in names):
    return SegmentStatus('recording', protected)
  try:
    if 'qcamera.ts' not in names or os.stat(os.path.join(path, 'qcamera.ts')).st_size <= 0:
      return SegmentStatus('no_video', protected)
  except OSError:
    return SegmentStatus('no_video', protected)
  return SegmentStatus('ready', protected)


STATE_TEXT = {'ready': 'Video available', 'recording': 'Still being recorded', 'deleted': 'Footage deleted',
              'no_video': 'No video saved', 'no_segment': 'No segment recorded'}


def status_label(status: SegmentStatus | None) -> str:
  if status is None:
    return 'Checking footage'
  text = STATE_TEXT.get(status.state, status.state)
  if status.state == 'deleted':
    return text
  return text + (', protected' if status.protected else ', not protected' if status.protected is False else '')


def events_path_for(log_root) -> Path:
  """eventd writes <media root>/review/events.jsonl, a sibling of the realdata directory (Paths.log_root())."""
  return Path(str(log_root)).parent / 'review' / 'events.jsonl'
