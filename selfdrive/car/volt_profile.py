"""Version-bound Volt qualification shared by startup, controls, and the UI."""

from dataclasses import asdict, replace
import hashlib
import json
import re
from pathlib import Path

from opendbc.car.gm.volt_longitudinal import VoltProfile, VoltFlags, profile_valid, supported
from openpilot.common.basedir import BASEDIR

BUNDLE_PATH = Path(BASEDIR) / 'selfdrive/car/volt_candidate.json'
SOURCE_FILES = (
  'selfdrive/car/volt_profile.py', 'selfdrive/car/card.py',
  'selfdrive/controls/lib/volt_trajectory.py', 'selfdrive/controls/lib/volt_stopping.py',
  'selfdrive/controls/lib/longcontrol.py', 'selfdrive/controls/lib/longitudinal_planner.py',
  'selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py',
  'opendbc/car/gm/volt_longitudinal.py', 'opendbc/car/gm/carcontroller.py', 'opendbc/car/gm/carstate.py',
  'selfdrive/test/longitudinal_maneuvers/volt_plant.py', 'selfdrive/test/longitudinal_maneuvers/volt_replay.py',
  'tools/profiling/volt_pressure_model.py', 'tools/profiling/volt_response_fit.py', 'tools/profiling/validate_volt_braking.py',
  'tools/profiling/volt_brake_diagnostics.py', 'tools/profiling/volt_function_fit.py', 'tools/profiling/volt_finish_metrics.py',
  'selfdrive/controls/lib/volt_polynomial.py', 'selfdrive/controls/controlsd.py', 'cereal/log.capnp',
)
VEHICLE_CHECKS = ('walking_stop', 'moderate_stop', 'holding', 'pedal_override', 'grade', 'engine_on', 'reduced_regen',
                  'driver_comfort', 'runtime_deadlines')


def digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_hashes(root=BASEDIR):
  return {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def qualification(checks, identity, vehicle=None):
  offline = [c for c in checks if c.get('stage') == 'offline']
  release = [c for c in checks if c.get('stage') == 'release']
  # Empty reports and old schemas cannot accidentally unlock a profile.
  required = {'response', 'traffic', 'nominal', 'stress', 'independent_response'}
  categories = {c.get('category') for c in offline}
  test_ready = bool(offline) and required <= categories and all(c.get('pass') is True for c in offline)
  vehicle = vehicle or {}
  rows = vehicle.get('checks', {})
  physical = (vehicle.get('profile_id') == identity and all(
    rows.get(name, {}).get('pass') is True
    and re.fullmatch(r'[0-9a-f]{8}--[0-9a-f]{10}', str(rows[name].get('route', '')))
    and re.fullmatch(r'[0-9a-f]{64}', str(rows[name].get('archive_sha256', '')))
    for name in VEHICLE_CHECKS))
  road_ready = bool(test_ready and physical and release and all(c.get('pass') is True for c in release))
  return {'test_ready': test_ready, 'vehicle_validated': bool(physical), 'road_ready': road_ready,
          'blocked_by': [c['name'] for c in checks if c.get('stage') == 'offline' and c.get('pass') is not True]}


def make_bundle(profile, curve, checks, *, vehicle=None, hashes=None, kind='personal'):
  calibration = asdict(replace(profile, validated=False, personal_validated=False))
  payload = {'version': 3, 'kind': kind, 'car': 'CHEVROLET_VOLT', 'profile': calibration, 'curve': curve,
             'sources': hashes if hashes is not None else source_hashes(), 'checks': checks}
  identity = digest({k: v for k, v in payload.items() if k != 'checks'})
  return {**payload, 'id': identity, 'vehicle': vehicle or {}, 'readiness': qualification(checks, identity, vehicle)}


def read_bundle(path=BUNDLE_PATH, *, raw=None, hashes=None):
  try:
    value = json.loads(Path(path).read_text()) if raw is None else json.loads(raw)
    payload = {key: value[key] for key in ('version', 'kind', 'car', 'profile', 'curve', 'sources', 'checks')}
    if (payload['version'] != 3 or payload['kind'] not in ('brake', 'personal') or payload['car'] != 'CHEVROLET_VOLT'
        or digest({k: v for k, v in payload.items() if k != 'checks'}) != value['id']
        or payload['sources'] != (source_hashes() if hashes is None else hashes)):
      return None
    profile = VoltProfile(**payload['profile'])
    if not profile_valid(profile):
      return None
    # Validate the curve without loading a generated solver in card or the UI.
    from openpilot.selfdrive.controls.lib.volt_polynomial import valid_model as validate_curve
    if (payload['kind'] == 'personal' and not validate_curve(payload['curve'])
        or payload['kind'] == 'brake' and payload['curve'] is not None):
      return None
    ready = qualification(payload['checks'], value['id'], value.get('vehicle'))
    value['readiness'] = ready
    value['calibration'] = replace(profile, version=value['id'][:16], validated=ready['road_ready'],
                                   personal_validated=ready['road_ready'] and payload['kind'] == 'personal')
    return value
  except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError, IndexError):
    return None


def runtime_bundle(CP):
  if not supported(CP) or not CP.flags & VoltFlags.SMOOTH:
    return None
  from openpilot.common.params import Params
  raw = Params().get('VoltLongitudinalActiveBundle')
  bundle = read_bundle(raw=raw) if raw else None
  matches_kind = bundle and bool(CP.flags & VoltFlags.PERSONAL) == (bundle['kind'] == 'personal')
  if matches_kind and (bundle['readiness']['road_ready'] or CP.flags & VoltFlags.TEST and bundle['readiness']['test_ready']):
    return bundle
  if CP.flags & (VoltFlags.TEST | VoltFlags.PERSONAL | VoltFlags.BUNDLE):
    raise RuntimeError('Selected Volt profile has no matching qualified startup snapshot')
  return None
