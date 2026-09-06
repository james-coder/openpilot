"""Pure selection helpers for the offline assisted-review pipeline."""
from bisect import bisect_right
from datetime import datetime
import math
from zoneinfo import ZoneInfo


def radar_at(snapshots, times, mono_ns):
  if mono_ns is None:
    return None
  i = bisect_right(times, mono_ns) - 1
  if i < 0 or mono_ns - times[i] > 150_000_000 or not snapshots[i]['healthy']:
    return None
  return {**snapshots[i], 'age_ms': round((mono_ns - times[i]) / 1e6, 2)}


def illumination(wall_ns, luminance):
  local = datetime.fromtimestamp(wall_ns / 1e9, ZoneInfo('America/Denver')) if wall_ns else None
  if local:
    hour = local.hour + local.minute / 60
    label = 'day' if 8 <= hour < 18 else 'dusk' if 5 <= hour < 8 or 18 <= hour < 21 else 'night'
    if label == 'dusk' and luminance > 100:
      label = 'day'
  else:
    label = 'day' if luminance >= 80 else 'night' if luminance < 25 else 'unknown'
  return {'value': label, 'source': 'local time + image brightness estimate',
          'local_time': local.isoformat(timespec='seconds') if local else None,
          'mean_luminance': round(luminance, 1)}


def associate_radar(radar, plate, camera):
  if not radar:
    return None
  focal = 2648 if camera == 'fcamera' else 567
  angle = math.atan((964 - (plate[0] + plate[2]) / 2) / focal)
  matches = []
  for p in radar['points']:
    target_angle = math.asin(max(-1, min(1, p['left_m'] / p['range_m'])))
    delta = abs(target_angle - angle)
    if delta < math.radians(5):
      matches.append((delta, p))
  if not matches:
    return None
  delta, point = min(matches, key=lambda x: x[0])
  return {**point, 'age_ms': radar['age_ms'], 'angular_error_deg': round(math.degrees(delta), 2),
          'association': 'approximate horizontal alignment; uncalibrated'}


def score_sample(row):
  # Favor real pixel detail first; OCR scores are not ground-truth accuracy.
  return row['width'] * math.sqrt(max(1, row['sharpness'])) * (0.7 + 0.3 * row['detection_confidence'])

