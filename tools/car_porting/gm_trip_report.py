"""Index local full route logs only. No network fallback, CAN socket, or requests."""
import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import warnings

from openpilot.selfdrive.car.gm_trip_monitor import TRIP_ROOT, SEGMENT, atomic_json
from openpilot.selfdrive.car.gm_trip_data import TripEvidence
from openpilot.selfdrive.car.gm_egr_data import MAX_REPORT_BYTES


def snapshot(path):
  with Path(path).open('rb') as stream:
    raw = stream.read(MAX_REPORT_BYTES + 1)
  if len(raw) > MAX_REPORT_BYTES:
    raise ValueError('Diagnostic snapshot exceeds limit')
  report = json.loads(raw)
  from openpilot.selfdrive.ui.layouts.settings.gm_diagnostics_data import valid_gm_report
  if not valid_gm_report(report, report.get('vehicle')):
    raise ValueError('Invalid diagnostic snapshot')
  return {'source': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'timestamp': report.get('timestamp'),
          'vehicle_identity_hash': hashlib.sha256(json.dumps(report['vehicle'], sort_keys=True).encode()).hexdigest(),
          'emissions': report.get('emissions', {}).get('ecus', {}), 'readings': report.get('readings', {}),
          'modules': report.get('modules', {}), 'context': report.get('context', {})}


def compare_snapshots(snapshots):
  phases = []
  for name in ('before', 'after', 'after_cleaning'):
    if name not in snapshots:
      continue
    item = snapshots[name]
    ecu = item['emissions'].get('7E8', {})
    phases.append({'phase': name, 'acquired_utc': item['timestamp'],
                   'ecm_dtc_statuses': {s: ecu.get(s, {'state': 'not_read'}) for s in ('stored', 'pending', 'permanent')},
                   'readiness': {k: item['readings'].get(k, {'state': 'not_read'}) for k in ('01:01', '01:41')},
                   'mode06': item['readings'].get('06:31', {'state': 'not_read'})})
  identities = {s['vehicle_identity_hash'] for s in snapshots.values()}
  calibrations = {tuple(s['readings'].get('09:04', {}).get('calibration_ids', [])) for s in snapshots.values()}
  return {'phases': phases, 'same_vehicle': len(identities) == 1 if phases else None,
          'same_calibration_lists': len(calibrations) == 1 and all(calibrations) if phases else None,
          'fresh_monitor_execution': 'unproven', 'note': 'Separate acquired results are not evidence of separate monitor executions. ' +
          'No data is relabeled as post-drive or post-cleaning automatically; phase association is supplied by the operator.'}


def index_route(root, route=None, *, cancelled=lambda: False, emit=None):
  from openpilot.tools.lib.logreader import LogReader
  root = Path(root)
  folders = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and SEGMENT.fullmatch(p.name) and (p / 'rlog.zst').is_file()]
  if not folders:
    raise ValueError('No preserved full rlogs; qlogs are deliberately not substituted')
  if route is None:
    route = max(folders, key=lambda p: (p / 'rlog.zst').stat().st_mtime).name.rsplit('--', 1)[0]
  folders = sorted((p for p in folders if p.name.rsplit('--', 1)[0] == route), key=lambda p: int(p.name.rsplit('--', 1)[1]))
  if not folders or len(folders) > 2000:
    raise ValueError('No matching route or route exceeds 2000-segment bound')
  evidence = TripEvidence()
  report = {'version': 1, 'profile': 'gm_trip', 'timestamp': datetime.now(UTC).isoformat(), 'route': route,
            'segments': [], 'problems': [], 'limitations': 'Passive context, not an EGR flow test or driving-safety assessment. ' +
            'Re-reading Mode 06 does not prove fresh monitor execution; snapshots are acquisition-timestamped only.'}
  numbers = [int(p.name.rsplit('--', 1)[1]) for p in folders]
  report['missing_segments'] = sorted(set(range(max(numbers) + 1)) - set(numbers))
  for folder in folders:
    if cancelled():
      raise InterruptedError('Indexing cancelled/on-road; raw logs are preserved')
    path = folder / 'rlog.zst'
    try:
      origin = json.loads((folder / 'source.json').read_text()) if (folder / 'source.json').exists() else {'source': str(path)}
      if Path(origin['source']).with_name('rlog.lock').exists():
        report['problems'].append(f'{folder.name}: still open; skipped')
        continue
      before = path.stat()
      if before.st_size > 32 * 1024**2:
        raise ValueError('Segment exceeds compressed-size bound')
      with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
      with warnings.catch_warnings(record=True) as caught:
        for i, event in enumerate(LogReader(str(path), sort_by_time=True, only_union_types=True)):
          if i % 1000 == 0 and cancelled():
            raise InterruptedError('Indexing stopped because vehicle is on-road')
          for row in evidence.feed(event):
            if emit:
              emit(folder.name, row)
        if caught:
          report['problems'].append(f'{folder.name}: reader reported corruption/truncation')
      after = path.stat()
      if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        report['problems'].append(f'{folder.name}: changed while reading; results provisional')
      report['segments'].append({'name': folder.name, 'file': str(path), 'sha256': digest, 'bytes': before.st_size})
    except (OSError, ValueError) as error:
      report['problems'].append(f'{folder.name}: {error}')
  report['context'] = evidence.finish()
  report['state'] = 'partial' if report['missing_segments'] or report['problems'] or not evidence.volt or not evidence.buses else 'complete'
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--root', type=Path, default=TRIP_ROOT)
  parser.add_argument('--route')
  parser.add_argument('--offroad-only', action='store_true')
  parser.add_argument('--csv', type=Path, help='Optional native-timestamp decoded stream, created exclusively; no raw CAN duplication')
  for name in ('before', 'after', 'after-cleaning'):
    parser.add_argument('--' + name, type=Path)
  args = parser.parse_args()
  os.nice(10)
  params = None
  if args.offroad_only:
    from openpilot.common.params import Params
    params = Params()
  def cancelled():
    return params is not None and params.get_bool('IsOnroad')
  csv_stream = None
  try:
    if cancelled():
      raise InterruptedError('Extraction is off-road only; normal raw logging continues while driving')
    emit = None
    if args.csv:
      csv_stream = args.csv.open('x')
      os.chmod(args.csv, 0o600)
      writer = csv.writer(csv_stream)
      writer.writerow(['segment', 'mono_s', 'source', 'signal', 'value'])
      def emit(segment, row):
        writer.writerows([segment, row['mono'], row['source'], k, v] for k, v in row['values'].items())
    report = index_route(args.root, args.route, cancelled=cancelled, emit=emit)
    report['diagnostic_snapshots'] = {name: snapshot(path) for name in ('before', 'after', 'after_cleaning') if (path := getattr(args, name))}
    report['diagnostic_comparison'] = compare_snapshots(report['diagnostic_snapshots'])
    report['comparison'] = 'Compare code statuses and returned Mode 06 records separately. Acquisition time is not monitor execution time. ' + \
                           'Snapshot association is user-selected; check vehicle/calibration identity and chronology before interpreting changes.'
    target = args.root / 'latest_report.json'
    atomic_json(target, report)
    atomic_json(args.root / f"report-{report['route']}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json", report)
    print(target)
  except (OSError, ValueError, InterruptedError) as error:
    parser.exit(1, f'{error}\n')
  finally:
    if csv_stream:
      csv_stream.close()


if __name__ == '__main__':
  main()
