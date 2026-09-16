"""Offline privacy-minimized validation. Explicit local files only; never contacts a car.

Missing evidence is not a pass. Kernel build, route and boot boundaries are
retained; no coordinate, VIN, modem identifier or arbitrary log text is emitted.
"""

import argparse
import bz2
from bisect import bisect_left, bisect_right
import hashlib
import io
import json
import math
from pathlib import Path

BUILD = '#4 SMP PREEMPT Tue Sep 15 23:01:32 MDT 2026'
MAX_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 1_000_000


def verdict(status, **evidence):
  return {'status': status, **evidence}


def finite(value):
  return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize(rows, complete=True):
  rows = sorted((r for r in rows if finite(r.get('t'))), key=lambda r: r['t'])
  started = [r['t'] for r in rows if r.get('started') is True]
  transitions = []
  previous = None
  for r in rows:
    if 'started' in r:
      if previous is not None and previous.get('started') is False and r['started'] is True and r['t'] - previous['t'] < 3:
        transitions.append(r['t'])
      previous = r
  cars = [r for r in rows if 'car' in r]
  car_states = [r['t'] for r in rows if r.get('car_state') is True]
  # carParams is route-local, not a cached persistent parameter. Require an
  # actual started/carState observation in the same route after its publication.
  active_cars = []
  car_times = [r['t'] for r in cars]
  for t in car_states:
    i = bisect_right(car_times, t) - 1
    j = bisect_left(started, t)
    if i >= 0 and any(abs(t - s) < 3 for s in started[max(0, j - 1) : j + 1]):
      active_cars.append(cars[i]['car'])
  wrong = any(c.get('brand') != 'gm' or 'VOLT' not in c.get('fingerprint', '').upper() for c in active_cars)
  manager = [r['missing_expected_count'] for r in rows if 'missing_expected_count' in r]
  pandas = [r['panda_fault_count'] for r in rows if 'panda_fault_count' in r]
  gps = [r for r in rows if 'fix' in r]
  longest = 0.0
  begin = last = None
  accuracy = []
  first_fix = None
  for r in gps:
    if not r['fix']:
      begin = last = None
      continue
    if first_fix is None:
      first_fix = r['t']
    if last is None or not 0 <= r['t'] - last < 3:
      begin = r['t']
    last = r['t']
    longest = max(longest, last - begin)
    if finite(r.get('accuracy')) and r['accuracy'] >= 0:
      accuracy.append(r['accuracy'])
  accuracy.sort()
  modem = [r['connected'] for r in rows if 'connected' in r]
  writes = sorted({r['aranet_write'] for r in rows if finite(r.get('aranet_write'))})
  engagement = [r for r in rows if r.get('engaged') is True]
  report = {
    'input_integrity': verdict('pass' if complete else 'fail'),
    'startup_transition': verdict('pass' if transitions else 'not_observed', count=len(transitions)),
    'vehicle_identification': verdict('fail' if wrong else 'pass' if active_cars else 'not_observed'),
    'expected_processes': verdict('fail' if any(manager) else 'pass' if manager else 'not_observed', samples=len(manager)),
    'panda_faults': verdict('fail' if any(pandas) else 'pass' if pandas else 'not_observed', samples=len(pandas)),
    'gps_sustained': verdict('pass' if longest >= 120 else 'not_observed', longest_fix_seconds=round(longest, 3), samples=len(gps)),
    'gps_accuracy_m': {
      'median': accuracy[len(accuracy) // 2] if accuracy else None,
      'p95': accuracy[min(len(accuracy) - 1, int(len(accuracy) * 0.95))] if accuracy else None,
    },
    'gps_acquisition_seconds': round(first_fix - transitions[0], 3) if transitions and first_fix is not None and first_fix >= transitions[0] else None,
    'cellular_state': verdict('pass' if modem and all(modem) else 'not_observed', samples=len(modem), disconnected_samples=modem.count(False)),
    'cellular_https': verdict('not_observed'),
    'aranet_writes': verdict(
      'pass' if len(writes) >= 2 else 'not_observed',
      distinct_timestamps=len(writes),
      maximum_gap_seconds=max((b - a for a, b in zip(writes, writes[1:], strict=False)), default=None),
    ),
    'ordinary_engagement': verdict('pass' if engagement else 'not_observed', observed_enabled_samples=len(engagement)),
    'physical_cold_boot': verdict('not_observed'),
  }
  if not complete:
    for value in report.values():
      if isinstance(value, dict) and value.get('status') == 'pass':
        value['status'] = 'not_observed'
  return report


def bounded_bytes(path):
  with path.open('rb') as f:
    data = f.read(MAX_BYTES + 1)
  if len(data) > MAX_BYTES:
    raise ValueError('input_size_limit')
  if data.startswith(b'BZh'):
    dec = bz2.BZ2Decompressor()
    data = dec.decompress(data, max_length=MAX_BYTES + 1)
    if not dec.eof or dec.unused_data:
      raise ValueError('truncated_or_oversized_bzip')
  elif data.startswith(b'\x28\xb5\x2f\xfd'):
    import zstandard

    # decompressobj exposes EOF, unlike an apparently successful truncated stream read.
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=True) as reader:
      expanded = reader.read(MAX_BYTES + 1)
    if len(expanded) > MAX_BYTES:
      raise ValueError('expanded_size_limit')
    # Bounded total output was checked above; validate each frame completes.
    remaining = data
    while remaining:
      check = zstandard.ZstdDecompressor().decompressobj()
      check.decompress(remaining)
      if not check.eof:
        raise ValueError('truncated_zstd_frame')
      remaining = check.unused_data
    data = expanded
  if len(data) > MAX_BYTES:
    raise ValueError('expanded_size_limit')
  return data


def route_rows(path):
  from cereal import log

  rows, meta = [], None
  for i, e in enumerate(log.Event.read_multiple_bytes(bounded_bytes(path))):
    if i >= MAX_EVENTS:
      raise ValueError('event_limit')
    kind, t = e.which(), e.logMonoTime / 1e9
    if kind == 'initData':
      if meta is not None:
        raise ValueError('multiple_route_metadata_records')
      d = e.initData
      meta = {'kernel': d.kernelVersion, 'revision': d.gitCommit, 'boot': d.bootlogId}
      if not meta['boot']:
        # This branch writes CurrentBootlog into logged params, not bootlogId.
        for entry in d.params.entries:
          if entry.key == 'CurrentBootlog' and len(entry.value) <= 128:
            meta['boot'] = bytes(entry.value).decode('ascii', errors='strict')
            break
      continue
    if not e.valid:
      continue
    r = {'t': t}
    if kind == 'deviceState':
      r['started'] = e.deviceState.started
    elif kind == 'carParams':
      r['car'] = {'brand': e.carParams.brand, 'fingerprint': e.carParams.carFingerprint}
    elif kind == 'carState':
      r['car_state'] = True
    elif kind == 'managerState':
      r['missing_expected_count'] = sum(p.shouldBeRunning and not p.running for p in e.managerState.processes)
    elif kind == 'pandaStates' and len(e.pandaStates):
      r['panda_fault_count'] = sum(len(p.faults) for p in e.pandaStates)
    elif kind == 'gpsLocationExternal':
      r.update(fix=e.gpsLocationExternal.hasFix, accuracy=e.gpsLocationExternal.horizontalAccuracy)
    elif kind == 'selfdriveState':
      r['engaged'] = e.selfdriveState.enabled
    else:
      continue
    rows.append(r)
  return meta, rows


def live_rows(path):
  rows, meta = [], None
  data = bounded_bytes(path)
  if not data.endswith(b'\n'):
    raise ValueError('truncated_jsonl')
  for i, line in enumerate(data.splitlines()):
    if i >= MAX_EVENTS or len(line) > 16384:
      raise ValueError('jsonl_limit')
    d = json.loads(line)
    if 'metadata' in d:
      if meta is not None or rows:
        raise ValueError('multiple_or_late_live_metadata')
      meta = d['metadata']
      continue
    r = {'t': d['elapsed_seconds']}
    fresh = d.get('fresh', {})
    if fresh.get('deviceState') and isinstance(d.get('started'), bool):
      r['started'] = d['started']
    if fresh.get('carState'):
      r['car_state'] = True
    # loaded_car_params is deliberately not accepted as a new fingerprint.
    if fresh.get('managerState') and 'missing_expected' in d:
      r['missing_expected_count'] = len(d['missing_expected'])
    if fresh.get('pandaStates') and d.get('pandas'):
      r['panda_fault_count'] = sum(p['fault_count'] for p in d['pandas'])
    if fresh.get('gpsLocationExternal') and 'gps' in d:
      r.update(fix=d['gps']['has_fix'], accuracy=d['gps']['horizontal_accuracy_m'])
    status = d.get('modem', {})
    if status.get('available') and 0 <= status.get('age_seconds', 1e9) < 3 and isinstance(status.get('connected'), bool):
      r['connected'] = status['connected']
    status = d.get('aranet', {})
    if status.get('available') and 0 <= status.get('age_seconds', 1e9) < 15 and finite(status.get('last_write')):
      r['aranet_write'] = status['last_write']
    rows.append(r)
  return meta, rows


def build_report(logs, live, build=BUILD):
  groups, sources = {}, []
  for kind, paths, reader in [('route', logs, route_rows), ('live', live, live_rows)]:
    for path in paths:
      source = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
      try:
        meta, rows = reader(path)
        if not meta or build not in meta.get('kernel', '') or not meta.get('boot'):
          sources.append({'source': source, 'status': 'not_observed', 'reason': 'missing_provenance_or_wrong_kernel'})
          continue
        route = path.parent.name.rsplit('--', 1)[0] if '--' in path.parent.name else str(path.resolve())
        key = (kind, route, meta['boot'], meta['kernel'], meta.get('revision', ''))
        groups.setdefault(key, []).extend(rows)
        sources.append({'source': source, 'status': 'pass', 'events': len(rows)})
      except Exception as exc:
        sources.append({'source': source, 'status': 'fail', 'reason': type(exc).__name__})
  # Conservative: no positive claims from an input collection with any corrupt file.
  complete = not any(s['status'] == 'fail' for s in sources)
  return {
    'schema': 1,
    'expected_build': build,
    'sources': sources,
    'groups': [
      {'id': hashlib.sha256(repr(k).encode()).hexdigest()[:12], 'kind': k[0], 'revision': k[4][:40], 'checks': summarize(v, complete)}
      for k, v in groups.items()
    ],
    'scope': 'Observations only; not certification of driving safety or USB containment',
  }


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--log', type=Path, action='append', default=[])
  parser.add_argument('--live-jsonl', type=Path, action='append', default=[])
  parser.add_argument('--kernel-build', default=BUILD)
  parser.add_argument('--output', type=Path)
  args = parser.parse_args()
  if not args.log and not args.live_jsonl or len(args.log) + len(args.live_jsonl) > 128 or not args.kernel_build:
    parser.error('Provide 1–128 explicit local files and a nonempty kernel build')
  text = json.dumps(build_report(args.log, args.live_jsonl, args.kernel_build), indent=2, allow_nan=False)
  if args.output:
    with args.output.open('x') as f:
      f.write(text + '\n')
  else:
    print(text)
