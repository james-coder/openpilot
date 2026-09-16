"""Offline full-rlog comparison; qlog is unsuitable for CAN utilization counts."""

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path

from openpilot.tools.volt_gateway.traffic_replay import frame_bits


def driving_state(speed, active):
  if speed is None or active is None:
    return 'unknown'
  if abs(speed) < .1:
    return 'stationary_active' if active else 'stationary_inactive'
  if abs(speed) < 1.:
    return 'creeping_active' if active else 'creeping_inactive'
  return 'driving_active' if active else 'driving_inactive'


def recent(history, timestamp):
  # Different logged services can arrive slightly out of timestamp order.
  # Never classify a CAN batch using a future state; retain the prior sample.
  return next((value for t, value in reversed(history) if 0 <= timestamp - t < 1_000_000_000), None)


def compare(path: Path):
  from opendbc.can import CANParser
  from openpilot.tools.lib.logreader import LogReader
  parser = CANParser('gm_global_a_object', [('F_LRR_Obj_Header', 0)], 1)
  bins = defaultdict(lambda: {'states': set(), 'bits': [0, 0, 0], 'frames': [0, 0, 0]})
  headers = defaultdict(Counter)
  speed_history, active_history = deque(maxlen=200), deque(maxlen=200)
  first = last = None
  speeds = []
  invalid = 0
  for event in LogReader(str(path)):
    t, kind = int(event.logMonoTime), event.which()
    if kind == 'carState':
      speed = float(event.carState.vEgo) if event.valid else None
      speed_history.append((t, speed))
      if speed is not None:
        speeds.append(speed)
    elif kind == 'selfdriveState':
      active = bool(event.selfdriveState.active) if event.valid else None
      active_history.append((t, active))
    elif kind == 'can':
      first = t if first is None else first
      last = t
      if t < first:
        raise ValueError('timestamp precedes segment start')
      state = driving_state(recent(speed_history, t), recent(active_history, t))
      row = bins[(t - first) // 1_000_000_000]
      row['states'].add(state)
      invalid += not event.valid
      for frame in event.can:
        bus, address, payload = int(frame.src), int(frame.address), bytes(frame.dat)
        if bus not in (0, 1, 2) or len(payload) > 8:
          continue
        row['bits'][bus] += frame_bits(address, len(payload))
        row['frames'][bus] += 1
        if bus == 1 and address == 1120 and len(payload) == 8:
          # DBC byte2 has mode[7:5], valid target count[4:0]. Use the
          # production parser too so this report does not invent a decoder.
          parser.update([(t, [(address, payload, bus)])])
          header = parser.vl['F_LRR_Obj_Header']
          headers[state]['count'] += 1
          headers[state]['nonzero_target_headers'] += header['FLRRNumValidTargets'] > 0
          headers[state]['sum_targets'] += int(header['FLRRNumValidTargets'])
          headers[state]['max_targets'] = max(headers[state]['max_targets'], int(header['FLRRNumValidTargets']))
          headers[state]['mode_' + str(int(header['FLRRModeCmdFdbk']))] += 1
  if first is None:
    raise ValueError('no full CAN log')
  groups = defaultdict(lambda: {'seconds': 0, 'bits': [0, 0, 0], 'frames': [0, 0, 0], 'peak_bits': [0, 0, 0]})
  for index, row in bins.items():
    # Last bin incomplete; first often has state initialization artifacts.
    if index == 0 or index >= (last - first) // 1_000_000_000:
      continue
    state = next(iter(row['states'])) if len(row['states']) == 1 else 'mixed_transition'
    group = groups[state]
    group['seconds'] += 1
    for bus in range(3):
      group['bits'][bus] += row['bits'][bus]
      group['frames'][bus] += row['frames'][bus]
      group['peak_bits'][bus] = max(group['peak_bits'][bus], row['bits'][bus])
  for group in groups.values():
    group['mean_percent'] = [bits / (group['seconds'] * 5000) for bits in group['bits']]
    group['peak_1s_percent'] = [bits / 5000 for bits in group['peak_bits']]
  with path.open('rb') as source:
    digest = hashlib.file_digest(source, 'sha256').hexdigest()
  return {'source': str(path), 'sha256': digest, 'invalid_can_batches': invalid,
          'min_kph': min(speeds, default=0) * 3.6, 'max_kph': max(speeds, default=0) * 3.6,
          'groups': dict(groups), 'radar_headers_by_state': {k: dict(v) for k, v in headers.items()},
          'method': 'full rlog RX sources 0/1/2, 500kbit/s, worst-case stuffing; full stable-state 1s bins',
          'limitations': ['not an electrical measurement or lossless-capture proof',
                          'radar mode numeric values are not interpreted as on/off',
                          'stationary is not proof of Park or ignition state',
                          'header counts include transition/partial seconds; utilization excludes them']}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('logs', type=Path, nargs='+')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  results = [compare(path) for path in args.logs]
  with args.output.open('x') as output:
    json.dump(results, output, indent=2)
    output.write('\n')
  print(json.dumps(results, indent=2))


if __name__ == '__main__':
  main()
