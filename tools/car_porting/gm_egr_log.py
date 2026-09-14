"""Start a gated parked EGR log, passively capture replies, or analyze saved evidence."""
import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time
import uuid

from openpilot.selfdrive.car.gm_egr_analysis import analyze
from openpilot.selfdrive.car.gm_egr_archive import archive_report
from openpilot.selfdrive.car.gm_egr_data import MAX_REPORT_BYTES
from openpilot.selfdrive.car.gm_egr_passive import EgrPassiveObserver


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  operation = parser.add_mutually_exclusive_group(required=True)
  operation.add_argument('--start', action='store_true', help='Request baseline + 120-second parked log through card; does not start engine')
  operation.add_argument('--analyze', type=Path, help='Analyze existing report; no vehicle connection')
  operation.add_argument('--capture-seconds', type=int, choices=range(1, 121), metavar='1..120',
                         help='Listen only, e.g. alongside an authorized GDS2 read-only session; no requests or flow control')
  args = parser.parse_args()
  if args.start:
    from openpilot.common.params import Params
    params = Params()
    status = params.get('GmScanStatus') or {}
    if status.get('state') == 'scanning' or params.get('ObdScanRequest') is not None:
      parser.error('Another scan/request exists; not replacing it')
    request_id = uuid.uuid4().hex
    params.put('ObdScanRequest', {'version': 1, 'command': 'log_egr', 'request_id': request_id,
                                'issued_mono': time.monotonic()}, block=True)
    print(f'Request queued, NOT proof of acceptance: {request_id}. Check GmScanStatus / GM details. ' +
          'Car must remain outdoors, on, parked, stationary, and disengaged. ' +
          'Completed evidence is archived under /data/media/0/diagnostics/gm; no engine-start command is sent.')
    return
  if args.analyze:
    try:
      with args.analyze.open('rb') as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
      if len(data) > MAX_REPORT_BYTES:
        raise ValueError('Report exceeds size limit')
      report = json.loads(data)
      result = analyze(report)
    except (OSError, ValueError, KeyError, TypeError) as error:
      parser.error(str(error))
  else:
    import cereal.messaging as messaging
    observer = EgrPassiveObserver()
    socket = messaging.sub_sock('can', timeout=1000)
    started = time.monotonic()
    report = {'version': 1, 'profile': 'gm_egr_passive', 'timestamp': datetime.now(UTC).isoformat(), 'started_mono': started,
              'limitations': 'Passive bus 0 ECM observer; no independent requests, flow control, or engine start. ' +
              'Silence is not unsupported. Raw traffic may contain vehicle identifiers; keep archive private.'}
    while time.monotonic() - started < args.capture_seconds and not observer.full:
      message = messaging.recv_one(socket)
      if message is None:
        continue
      now = message.logMonoTime / 1e9
      if not started <= now <= time.monotonic():
        continue
      for frame in message.can:
        observer.feed(now, frame.address, frame.dat, frame.src)
    report.update(samples=observer.samples, evidence=observer.evidence, state='capture_limit' if observer.full else 'complete')
    location = archive_report(report)
    result = analyze(report)
    result['archive'] = location
  print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
  main()
