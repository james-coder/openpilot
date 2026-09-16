"""Bounded passive observer. No CAN/DIAG TX, services, resets or param writes.

Run manually at a normal startup; stdout is privacy-minimized JSONL. Offroad
absence of GPS/carState is NOT failure. No new boot/manager hook is installed.
"""
import argparse
import json
import os
from pathlib import Path
import time


def freshness(seen, valid, age):
  return bool(seen and valid and 0 <= age < 3)


def local_status(path, keys):
  try:
    with Path(path).open('rb') as f:
      data = f.read(4097)
    if len(data) > 4096:
      return {'available': False}
    obj = json.loads(data)
    if not isinstance(obj, dict):
      return {'available': False}
    # Only explicitly requested scalar fields. No modem IDs or GPS coordinates.
    return {'available': True, **{k: obj[k] for k in keys if k in obj and isinstance(obj[k], (bool, int, float))}}
  except (OSError, ValueError, TypeError):
    return {'available': False}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--seconds', type=int, choices=range(1, 601), default=120)
  args = parser.parse_args()
  os.nice(19)
  try:
    os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
  except OSError:
    pass
  from cereal import messaging
  from opendbc.car import structs
  from openpilot.common.params import Params
  params = Params()
  sm = messaging.SubMaster(['deviceState', 'pandaStates', 'managerState', 'gpsLocationExternal', 'carState'])
  start = time.monotonic()
  while time.monotonic()-start < args.seconds:
    sm.update(0)
    now = time.monotonic()
    fresh = {k: freshness(sm.seen[k], sm.valid[k], now-sm.recv_time[k]) for k in sm.services}
    report = {'elapsed_seconds': round(now-start, 1), 'fresh': fresh}
    if fresh['deviceState']:
      report['started'] = sm['deviceState'].started
    if report.get('started') and fresh['carState']:
      raw = params.get('CarParams')
      if raw and len(raw) <= 1024*1024:
        try:
          with structs.CarParams.from_bytes(raw) as cp:
            report['loaded_car_params'] = {'brand': str(cp.brand)[:64], 'fingerprint': str(cp.carFingerprint)[:128]}
        except Exception:
          report['loaded_car_params'] = {'parse_failed': True}
    if fresh['pandaStates']:
      report['pandas'] = [{'ignition': p.ignitionLine or p.ignitionCan, 'controls_allowed': p.controlsAllowed,
                           'fault_count': len(p.faults)} for p in sm['pandaStates']]
    if fresh['managerState']:
      report['missing_expected'] = [p.name for p in sm['managerState'].processes if p.shouldBeRunning and not p.running]
      report['gps_process'] = [{'running': p.running, 'expected': p.shouldBeRunning}
                               for p in sm['managerState'].processes if p.name == 'qcomgpsd']
    if fresh['gpsLocationExternal']:
      report['gps'] = {'has_fix': sm['gpsLocationExternal'].hasFix,
                       'horizontal_accuracy_m': sm['gpsLocationExternal'].horizontalAccuracy}
    report['modem'] = local_status('/dev/shm/modem', ['connected', 'retry_count'])
    report['aranet'] = local_status('/data/aranet/status.json', ['time', 'last_advertisement', 'last_write'])
    print(json.dumps(report), flush=True)
    time.sleep(1)


if __name__ == '__main__':
  main()
