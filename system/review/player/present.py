"""Text shown on the Dashcam screen, built from the model so the widget only draws it. Pure."""
from openpilot.system.review.player.events import EventRow, SegmentStatus, describe_time, kinds_label, peaks_label, status_label
from openpilot.system.review.player.timeline import format_clock

MPS_TO_KPH = 3.6
MPS_TO_MPH = 2.236936


def speed_text(mps: float, metric: bool) -> str:
  return f'{mps * MPS_TO_KPH:.0f} km/h' if metric else f'{mps * MPS_TO_MPH:.0f} mph'


def row_lines(row: EventRow, status: SegmentStatus | None, wall_now: float | None = None) -> tuple:
  """(title, detail, footage) for one list row."""
  detail = kinds_label(row.kinds)
  peaks = peaks_label(row.peaks)
  if peaks:
    detail += '  ' + peaks
  footage = status_label(status)
  if row.segment:
    footage = f'{"--".join(row.segment.rsplit("--", 2)[-2:])}  {footage}'
  return describe_time(row.wall, wall_now), detail, footage


def info_lines(view, metric: bool, wall_now: float | None = None) -> list:
  """[(text, tone)] for the panel beside the video. tone: 'normal' | 'dim' | 'warn' | 'good'."""
  row = next((r for r in view.rows if r.key == view.selected), None)
  out = [('Low-res preview', 'dim')]
  if row is None:
    return out + [('Pick an event on the left.', 'normal')]
  out.append((describe_time(row.wall, wall_now), 'normal'))
  out.append((kinds_label(row.kinds), 'normal'))
  peaks = peaks_label(row.peaks)
  if peaks:
    out.append((peaks, 'dim'))
  status = view.segment_status
  protected = status is not None and status.protected
  out.append((status_label(status), 'good' if protected else 'dim'))
  if view.clip_state == 'ready':
    out.append((f'{format_clock(view.position)} / {format_clock(view.duration)}   {view.rate:g}x', 'normal'))
    if view.event_time is None:
      out.append(('Event time unknown', 'warn'))
    now = view.now
    if now:
      flags = ['BRAKE'] if now['brake'] else []
      flags += ['LEFT'] if now['left'] else []
      flags += ['RIGHT'] if now['right'] else []
      out.append((speed_text(now['speed'], metric) + ('   ' + ' '.join(flags) if flags else ''), 'normal'))
    elif view.telemetry is None:
      out.append(('No speed data', 'dim'))
    if view.buffering:
      out.append(('Loading frames...', 'warn'))
    if view.in_gap:
      out.append(('Video is damaged here', 'warn'))
  if view.hot:
    out.append(('Hot: decoding paused', 'warn'))
  if view.message and view.clip_state == 'ready':
    out.append((view.message, 'warn'))
  if view.notice and not (view.hot and 'hot' in view.notice.lower()):   # the hot line above already says it
    out.append((view.notice, 'warn'))
  return out


def list_empty_text(events_state: str) -> tuple:
  """(headline, detail) for an empty list."""
  if events_state == 'loading':
    return 'Loading events...', ''
  if events_state == 'missing':
    return 'No saved events yet', 'Hard braking, ABS stops and\nextreme g-force events are\nsaved while driving.'
  if events_state == 'unreadable':
    return 'Event list unreadable', 'The file could not be read. Driving is unaffected.'
  return 'No events', 'Nothing has been saved yet.'
