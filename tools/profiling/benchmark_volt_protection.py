"""Host-only GC-disabled production arbitration benchmark; no device qualification."""

import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
import psutil

from openpilot.tools.profiling.volt_protection_scenarios import ProtectionBench


def benchmark(samples=20000):
  bench = ProtectionBench()
  for i in range(1000):
    if i % 5 == 0:
      bench.lead(24., 2.)
      bench.lead(22., 2., identity=2, second=True)
    bench.step()
  gc.collect()
  enabled = gc.isenabled()
  gc.disable()
  process = psutil.Process()
  start = process.memory_info().rss
  timing = np.zeros(samples)
  try:
    for i in range(samples):
      if i % 5 == 0:
        bench.lead(24., 2.)
        bench.lead(22., 2., identity=2, second=True)
      before = time.perf_counter_ns()
      bench.step()
      timing[i] = (time.perf_counter_ns()-before)/1e6
    growth = process.memory_info().rss-start
    unreachable = gc.collect()
  finally:
    if enabled:
      gc.enable()
  return {'scope': 'Host synthetic controls + GM CAN packing with two distinct leads requiring bounded search; no whole-device qualification',
          'device_qualified': False, 'samples': samples, 'rss_growth_bytes': growth, 'unreachable_objects': unreachable,
          'median_ms': float(np.median(timing)), 'p99_ms': float(np.percentile(timing, 99)), 'maximum_ms': float(np.max(timing)),
          'cycles_over_10ms': int(np.sum(timing >= 10.))}


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('output', type=Path)
  args = parser.parse_args()
  result = benchmark()
  args.output.write_text(json.dumps(result, indent=2)+'\n')
  print(json.dumps(result))
