"""Source-bound, separately qualified protection startup snapshot."""

from dataclasses import asdict
import json
import math
from pathlib import Path
import re

from opendbc.car.gm.volt_longitudinal import supported
from opendbc.car.gm.volt_protection import ProtectionAuthority
from openpilot.common.basedir import BASEDIR
from openpilot.selfdrive.car.volt_profile import source_hashes, digest
from openpilot.selfdrive.controls.lib.volt_collision import Envelope
from openpilot.selfdrive.controls.lib.volt_protection import ProtectionCalibration

BUNDLE_PATH = Path(BASEDIR) / 'selfdrive/car/volt_protection_candidate.json'
MONITOR = 1 << 20
ACTUATE = 1 << 21
TEST = 1 << 22
REQUIRED_TEST = ('geometry', 'target_association', 'friction_response', 'can_arbitration', 'scenario_matrix', 'fault_handling')
REQUIRED_ROAD = ('soft_targets', 'driver_overrides', 'grade_grip_regen', 'independent_review')


def evidence_matches(item, identity):
  return (isinstance(item, dict) and item.get('pass') is True and item.get('profile_id') == identity
          and re.fullmatch('[0-9a-f]{64}', str(item.get('archive_sha256', ''))) is not None)


def runtime_valid(evidence, identity):
  try:
    if (not evidence_matches(evidence, identity) or evidence['device'] != 'comma3'
        or not math.isfinite(evidence['duration_seconds']) or evidence['duration_seconds'] < 60
        or evidence['includes_cold_start'] is not True or evidence['includes_candidate_work'] is not True
        or evidence['memory_pressure'] is not False or evidence['thermal_throttling'] is not False):
      return False
    expected = {'controlsd': (4, 53, 10.), 'card': (4, 53, 10.), 'selfdrived': (4, 53, 10.),
                'plannerd': (5, 51, 50.), 'radard': (5, 51, 50.), 'modeld': (7, 54, 50.)}
    for name, (core, priority, period) in expected.items():
      p = evidence['processes'][name]
      if (p['core'] != core or p['fifo_priority'] != priority or p['deadline_misses'] != 0
          or not 0 < p['maximum_cycle_ms'] < period or not math.isfinite(p['samples']) or p['samples'] < 60000 / period):
        return False
    return True
  except (KeyError, ValueError, TypeError):
    return False


def make_bundle(calibration, evidence=None):
  data = asdict(calibration)
  data['authority'].pop('profile_id')
  payload = {'version': 1, 'car': 'CHEVROLET_VOLT', 'calibration': data, 'sources': source_hashes()}
  return {**payload, 'id': digest(payload), 'evidence': evidence or {}}


def read_bundle(path=BUNDLE_PATH, *, raw=None):
  try:
    value = json.loads(Path(path).read_text() if raw is None else raw)
    payload = {k: value[k] for k in ('version', 'car', 'calibration', 'sources')}
    if payload['version'] != 1 or payload['car'] != 'CHEVROLET_VOLT' or payload['sources'] != source_hashes() or digest(payload) != value['id']:
      return None
    data = dict(payload['calibration'])
    data['envelope'] = Envelope(**data['envelope'])
    data['authority'] = ProtectionAuthority(profile_id=value['id'], **data['authority'])
    cal = ProtectionCalibration(**data)
    if not cal.valid():
      return None
    evidence = value.get('evidence', {})
    monitor = runtime_valid(evidence.get('runtime', {}), value['id'])
    test = monitor and all(evidence_matches(evidence.get(k), value['id']) for k in REQUIRED_TEST)
    road = test and all(evidence_matches(evidence.get(k), value['id']) for k in REQUIRED_ROAD)
    return {**value, 'calibration': cal, 'readiness': {'monitor_ready': monitor, 'test_ready': test, 'road_ready': road}}
  except (OSError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
    return None


def configure(cp, mode, bundle):
  cp.flags &= ~(MONITOR | ACTUATE | TEST)
  if not supported(cp) or not bundle:
    return 'unavailable'
  ready = bundle['readiness']
  if mode == 'test' and ready['test_ready']:
    cp.flags |= MONITOR | ACTUATE | TEST
    return 'test'
  if mode == 'enabled' and ready['road_ready']:
    cp.flags |= MONITOR | ACTUATE
    return 'enabled'
  if mode in ('monitor', 'test', 'enabled') and ready['monitor_ready']:
    cp.flags |= MONITOR
    return 'monitor'
  return 'unavailable'


def runtime_bundle(cp, params):
  if not supported(cp) or not cp.flags & MONITOR:
    return None
  raw = params.get('VoltProtectionActiveBundle')
  bundle = read_bundle(raw=raw) if raw else None
  stage = 'test_ready' if cp.flags & TEST else 'road_ready' if cp.flags & ACTUATE else 'monitor_ready'
  if not bundle or not bundle['readiness'][stage]:
    raise RuntimeError('Volt protection has no matching qualified startup snapshot')
  return bundle
