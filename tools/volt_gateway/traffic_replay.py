"""Offline route-CAN load replay. Never opens a CAN/USB interface or sends traffic.

Batch timestamps are NOT wire timestamps. Stuffing bounds and synthetic faults
are explicitly models, not measurements of arbitration or CAN error counters.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import struct

from openpilot.tools.volt_gateway.adaptive import AdaptiveBudget, Conditions
from openpilot.tools.volt_gateway.protocol import Observation, StreamReceiver, stream_encode
from openpilot.tools.volt_gateway.simulation import Scheduler


WINDOW_NS = 20_000_000


def frame_bits(address: int, dlc: int) -> int:
  if not 0 <= address <= 0x1FFFFFFF or not 0 <= dlc <= 8:
    raise ValueError('not a classic CAN data frame')
  # SOF through CRC sequence subject to stuffing, plus delimiter/ACK/EOF/IFS.
  stuffable = (54 if address > 0x7FF else 34) + 8 * dlc
  return stuffable + (stuffable - 1) // 4 + 13


def extract(paths: list[Path], seconds: int = 180) -> dict:
  from openpilot.tools.lib.logreader import LogReader
  if not 1 <= seconds <= 600:
    raise ValueError('duration bounded to ten minutes')
  windows = {bus: [0] * (seconds * 50) for bus in range(3)}
  counts, excluded, ids = Counter(), Counter(), {bus: set() for bus in range(3)}
  first = last = None
  invalid = regressions = codec_checks = 0
  receiver = StreamReceiver()
  sources = []
  for path in paths:
    with path.open('rb') as source:
      sources.append({'name': path.name, 'sha256': hashlib.file_digest(source, 'sha256').hexdigest()})
    for event in LogReader(str(path)):
      if event.which() != 'can':
        continue
      timestamp = int(event.logMonoTime)
      if first is None:
        first = timestamp
      if last is not None and timestamp < last:
        regressions += 1
        continue
      if timestamp - first >= seconds * 1_000_000_000:
        break
      last = timestamp
      invalid += not event.valid
      index = (timestamp - first) // WINDOW_NS
      for frame in event.can:
        bus, address, payload = int(frame.src), int(frame.address), bytes(frame.dat)
        # TX-return/rejected flag values are NOT additional received OEM frames.
        if bus not in windows or len(payload) > 8:
          excluded[str(bus)] += 1
          continue
        windows[bus][index] += frame_bits(address, len(payload))
        counts[bus] += 1
        ids[bus].add(address)
        # Exercise real payload/ID/length combinations without retaining payloads
        # in the public load fixture; private source rlogs retain every frame.
        if counts[bus] % 100 == 1:
          record = Observation(bus, address, address > 0x7FF, False, len(payload), payload,
                               (timestamp - first) // 1000, codec_checks)
          for encoded in stream_encode(record):
            decoded = receiver.feed(encoded, (timestamp - first) / 1e9)
          if decoded != record:
            raise AssertionError('real CAN record failed codec roundtrip')
          codec_checks += 1
  if first is None or last is None:
    raise ValueError('no CAN data')
  # Drop the final partially observed window rather than imply a full window.
  complete = min(seconds * 50, (last - first) // WINDOW_NS)
  if complete < 50:
    raise ValueError('less than one second of CAN evidence')
  return {'version': 1, 'sources': sources, 'duration_seconds': complete / 50,
          'invalid_batches': invalid, 'timestamp_regressions': regressions,
          'excluded_sources': dict(excluded), 'codec_roundtrips': codec_checks,
          'buses': {str(bus): {'frames': counts[bus], 'unique_ids': len(ids[bus]),
                              'window_bits': values[:complete]} for bus, values in windows.items()},
          'limitations': ['20ms receive-batch bins, not electrical timing',
                          'worst-case stuffing estimate; not measured bus utilization',
                          'no capture-loss, arbitration-loss, ACK or error-frame evidence',
                          'standard/extended inferred from ID; RTR not present in route schema',
                          'frame counts include the final partial window; replay excludes it']}


def replay(bits: list[int], *, seed: int = 2017, fault: bool = False) -> dict:
  if not bits or len(bits) > 30_000 or any(type(v) is not int or v < 0 for v in bits):
    raise ValueError('invalid bounded load fixture')
  rng = random.Random(seed)
  scheduler, budget = Scheduler(), AdaptiveBudget()
  ready = Conditions(True, True, True, True, True, True, False)  # simulated only
  pending, latencies = {}, []
  sent = updates = offered = max_queue = own_bits = 0
  reasons = Counter()
  for tick in range(len(bits) * 20):
    now = tick / 1000
    window = tick // 20
    # Synthetic 1-second bus-off/error episode every 30 seconds. No physical
    # recovery behavior claimed; stop model transmissions while unavailable.
    unavailable = fault and tick % 30_000 >= 29_000
    if tick % 20 == 0:
      conditions = ready if not unavailable else Conditions()
      budget.sample(now, bits[window] + own_bits, conditions)
      own_bits = 0
      reasons[budget.reason] += 1
    # Seeded synthetic GMLAN source: noise, changing IDs/data and periodic bursts.
    # Its frame count is NOT a statement about real Volt SWCAN behavior.
    if rng.random() < (.5 if tick % 10_000 < 1000 else .03):
      record = Observation(3, 0x10000000 + rng.randrange(256), True, False, 8,
                           rng.randbytes(8), tick * 1000, offered)
      scheduler.offer(stream_encode(record), now, control=False)
      offered += 1
    if tick % 1000 == 0:
      if scheduler.offer([struct.pack('!II', tick, i) for i in range(8)], now, control=True, indivisible=True):
        pending[tick] = now
    # Synthetic service opportunities proportional to each measured load bin.
    # NOT bit-level arbitration and NOT a proposal for a firmware idle detector.
    busy = unavailable or rng.random() < min(1., bits[window] / 10_000)
    output = scheduler.step(now, busy=busy)
    if output:
      sent += 1
      own_bits += 135
      if output[1]:
        transaction, fragment = struct.unpack('!II', output[0])
        if fragment == 7:
          latencies.append(now - pending.pop(transaction))
    if not busy and output is None and budget.take(now):
      updates += 1
      own_bits += 135
    max_queue = max(max_queue, len(scheduler.control) + len(scheduler.data) + bool(scheduler.active))
  return {'seed': seed, 'synthetic_error_episode': fault, 'seconds': len(bits) / 50,
          'estimated_mean_oem_load': sum(bits) / (len(bits) * 10_000),
          'peak_20ms_estimate': max(bits) / 10_000,
          'bins_exceeding_physical_capacity': sum(v > 10_000 for v in bits),
          'synthetic_gmlan_records': offered, 'scheduler_tx_frames': sent,
          'update_admitted_frames': updates, 'max_queued_packets': max_queue,
          'completed_control_responses': len(latencies), 'pending_control_responses': len(pending),
          'max_control_response_seconds': max(latencies, default=None),
          'scheduler_counters': dict(scheduler.counters), 'budget_reasons': dict(reasons),
          'hardware_emulation': False, 'vehicle_conditions_verified': False}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('logs', type=Path, nargs='+')
  parser.add_argument('--seconds', type=int, default=180)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  evidence = extract(args.logs, args.seconds)
  evidence['replays'] = {bus: [replay(data['window_bits'], fault=fault) for fault in (False, True)]
                         for bus, data in evidence['buses'].items()}
  with args.output.open('x') as output:
    json.dump(evidence, output, indent=2)
    output.write('\n')
  print(json.dumps(evidence['replays'], indent=2))


if __name__ == '__main__':
  main()
