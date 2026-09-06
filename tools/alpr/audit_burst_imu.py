"""Recorded IMU audit; run with the openpilot environment. No vehicle I/O."""

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
import zstandard
from cereal import log


def main():
  parser = argparse.ArgumentParser(description='Audit recorded IMU and camera metadata for a plate burst')
  parser.add_argument('data', type=Path)
  parser.add_argument('--encounter', required=True)
  args = parser.parse_args()
  base = args.data
  q = json.loads((base / 'assisted-v1/queue.json').read_text())
  vehicle = next(e for e in q['encounters'] if e['id'] == args.encounter)
  name = vehicle['samples'][0]['segment']
  numbers = sorted(s['frame'] for s in vehicle['samples'])
  camera = vehicle['samples'][0]['camera']
  index = json.loads((base / '2026-09-study' / name / 'index.json').read_text())
  start = index['frames'][camera][str(numbers[0])]['mono_ns']
  end = index['frames'][camera][str(numbers[-1])]['mono_ns']
  out = {'window_seconds': (end - start) / 1e9, 'gyroscope': [], 'accelerometer': [], 'camera': []}
  with (base / '2026-09-study' / name / 'rlog.zst').open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as stream:
    raw = stream.read()
  for e in log.Event.read_multiple_bytes(raw):
    kind = e.which()
    if kind in ['gyroscope', 'accelerometer']:
      r = getattr(e, kind)
      t = r.timestamp
      if start - 30_000_000 <= t <= end + 30_000_000:
        union = r.which()
        vec = list(getattr(r, union).v)
        out[kind].append({'mono_ns': t, 'source': str(r.source), 'values': vec})
    elif kind == 'roadCameraState':
      r = getattr(e, kind)
      if start - 30_000_000 <= r.timestampSof <= end + 30_000_000:
        out['camera'].append(
          {
            'mono_ns': r.timestampSof,
            'frame_id': r.frameId,
            'sensor': str(r.sensor),
            'integ_lines': r.integLines,
            'gain': r.gain,
            'exposure_percent': r.exposureValPercent,
            'sof_to_eof_ms': (r.timestampEof - r.timestampSof) / 1e6,
          }
        )
  summary = {'window_seconds': out['window_seconds']}
  for kind in ['gyroscope', 'accelerometer']:
    rows = out[kind]
    times = sorted(r['mono_ns'] for r in rows)
    norms = [math.sqrt(sum(v * v for v in r['values'])) for r in rows]
    summary[kind] = {
      'count': len(rows),
      'median_rate_hz': 1e9 / statistics.median(b - a for a, b in zip(times, times[1:], strict=False)) if len(times) > 1 else None,
      'norm_median': statistics.median(norms) if norms else None,
      'norm_max': max(norms) if norms else None,
      'source': rows[0]['source'] if rows else None,
    }
  if out['gyroscope']:
    rows = sorted(out['gyroscope'], key=lambda r: r['mono_ns'])
    angle = 0
    for a, b in zip(rows, rows[1:], strict=False):
      if start <= a['mono_ns'] <= end:
        angle += math.sqrt(sum(v * v for v in a['values'])) * (b['mono_ns'] - a['mono_ns']) / 1e9
    summary['gyro_integrated_norm_rad'] = angle
    summary['rough_rotation_pixel_scale'] = 2648 * angle
  summary['camera'] = out['camera'][::4]
  samples = sorted(vehicle['samples'], key=lambda r: r['frame'])
  complete = [s for s in samples if s["box"][0] > 0 and s["box"][2] < 1928]
  first, last = complete[0], complete[-1]
  summary['plate_motion_px_per_second'] = abs(sum(last['box'][::2]) / 2 - sum(first['box'][::2]) / 2) / ((last['frame'] - first['frame']) / 20)
  out['summary'] = summary
  folder = base / 'assisted-v1/fusion' / hashlib.sha256(args.encounter.encode()).hexdigest()[:12]
  folder.mkdir(parents=True, exist_ok=True)
  (folder / 'imu-audit.json').write_text(json.dumps(out, indent=2) + '\n')
  print(json.dumps(summary, indent=2))


if __name__ == "__main__":
  main()
