"""Synthetic CAN inspector decode/memory profile. Opens no CAN socket."""
import argparse
from collections import deque
import gc
import json
from pathlib import Path
import statistics
import time

from opendbc.can.packer import CANPacker
from opendbc.car.gm.values import CAR
from openpilot.selfdrive.ui.layouts.settings.can_inspection import InspectionSession


def rss_bytes():
  return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))*1024


def profile(iterations):
  session = InspectionSession(CAR.CHEVROLET_VOLT)
  packer = CANPacker('gm_global_a_powertrain_generated')
  first = (0, 201, 'EngineRPM')
  session.select_graph(first, 0)
  session.select_graph((0, 309, 'PRNDL'), 1)
  session.inspect_bits((0, 309))
  # Exercise the unknown-ID cap as well as the decoded messages.
  session.ingest([(time.monotonic_ns(), [(0x10000+i, b'\0'*8, bus) for bus in range(3) for i in range(300)])])
  timings = deque(maxlen=4096)
  memory = []
  origin = time.monotonic_ns()
  for i in range(iterations):
    rpm = packer.make_can_msg('ECMEngineStatus', 0, {'EngineRPM': 1000+i%2000})
    gear = packer.make_can_msg('ECMPRDNL', 0, {'PRNDL': i%4})
    start = time.monotonic()
    session.ingest([(origin+i*5_000_000, [rpm, gear]*10)])
    timings.append((time.monotonic()-start)*1000)
    if i % 20 == 0:
      session.capture()
    if i > 8000 and i % 4000 == 0:
      session.toggle_freeze()
      session.capture()
      session.toggle_freeze()
      gc.collect()
      memory.append(rss_bytes())
  assert len(session.histories) == 2
  assert all(len(buf.samples) <= 6000 for buf in session.histories.values())
  assert len(session.snapshot.messages) <= 3*256+113
  growth = max(memory)-min(memory) if memory else 0
  assert growth < 8*1024*1024, f'RSS grew {growth} bytes after warmup'
  return {'iterations': iterations, 'frames_per_batch': 20,
          'batch_ms': {'median': statistics.median(timings), 'p99': sorted(timings)[int(len(timings)*.99)], 'max': max(timings)},
          'rss_samples_bytes': memory, 'rss_range_after_warmup_bytes': growth,
          'graph_samples': [len(buf.samples) for buf in session.histories.values()], 'observed_ids': len(session.snapshot.messages)}


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--iterations', type=int, default=24000)
  print(json.dumps(profile(parser.parse_args().iterations), indent=2))
