"""Replay recorded radar inputs without sockets or vehicle I/O; measure memory growth."""
import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import psutil
import zstandard
from cereal import log
from openpilot.selfdrive.controls.radard import RadarD


class RecordedInputs:
  def __init__(self):
    self.seen = {'modelV2': True}
    self.logMonoTime = {'modelV2': 0, 'carState': 0, 'liveTracks': 0}
    self.recv_frame = {'carState': 0}
    self.data = {}

  def __getitem__(self, key):
    return self.data[key]

  def all_checks(self):
    return True


class SerializeOnly:
  def send(self, service, message):
    message.to_bytes()


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('logs', nargs='+', type=Path)
  p.add_argument('--hours', type=float, default=4)
  p.add_argument('--real-time', action='store_true')
  p.add_argument('--output', type=Path, required=True)
  args = p.parse_args()
  if args.hours < 1 / 60:
    p.error('measure at least one minute for a memory slope')
  snapshots, latest = [], {}
  incomplete = []
  for path in args.logs:
    with path.open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as reader:
      data = reader.read()
    try:
      for e in log.Event.read_multiple_bytes(data):
        service = e.which()
        if service in ('modelV2', 'carState', 'liveTracks'):
          latest[service] = getattr(e, service)
          if service == 'modelV2' and len(latest) == 3:
            snapshots.append(latest.copy())
    except Exception as e:
      incomplete.append({'path': str(path), 'error': type(e).__name__})
  if not snapshots:
    p.error('no model/car/radar snapshots; use rlog, not qlog')
  sm, rd, pm = RecordedInputs(), RadarD(), SerializeOnly()

  def tick(i):
    sm.data = snapshots[i % len(snapshots)]
    sm.recv_frame['carState'] = i
    for service in sm.logMonoTime:
      sm.logMonoTime[service] = i * 50_000_000
    rd.update(sm, sm['liveTracks'])
    rd.publish(pm)

  for i in range(1000):
    tick(i)
  gc.collect()
  gc.disable()
  process = psutil.Process()
  samples = []
  start = time.monotonic()
  for i in range(round(args.hours * 3600 * 20) + 1):
    tick(i + 1000)
    if i % 1200 == 0:
      mem = process.memory_full_info()
      samples.append({'hours': i / 72000, 'rss': mem.rss, 'pss': getattr(mem, 'pss', None)})
    if args.real_time:
      time.sleep(max(0, start + i / 20 - time.monotonic()))
  growth = samples[-1]['rss'] - samples[0]['rss']
  slope = float(np.polyfit([s['hours'] for s in samples], [s['rss'] / 1024**2 for s in samples], 1)[0])
  result = {'simulated_hours': args.hours, 'real_time': args.real_time, 'elapsed_seconds': time.monotonic() - start,
            'input_snapshots': len(snapshots), 'incomplete_inputs': incomplete,
            'rss_growth_bytes': growth, 'rss_slope_mib_per_hour': slope,
            'unreachable_after_run': gc.collect(), 'samples': samples,
            'passed': growth < 16 * 1024**2 and slope < 1.0}
  args.output.write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, indent=2))
  raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
  main()
