from copy import deepcopy
from dataclasses import replace
import json
import numpy as np
import pytest
from opendbc.car.gm.volt_longitudinal import PROFILE, configure
from openpilot.selfdrive.car.volt_profile import make_bundle, read_bundle, qualification, runtime_bundle, VEHICLE_CHECKS
from openpilot.selfdrive.controls.lib.volt_trajectory import StopTrajectory, validate_curve
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import metrics, VoltPlant, volt_params
from openpilot.tools.profiling.volt_pressure_model import pressure_trace, PressureDynamics, allocator_profile, evaluate_pressure_response
from openpilot.tools.profiling.tests.test_volt_replay import curve_fixture
from openpilot.tools.profiling.volt_response_fit import approach_curve, prepare


def curve():
  return approach_curve(*curve_fixture())


def polynomial_curve():
  from openpilot.selfdrive.controls.lib.volt_polynomial import MODEL
  return {'version': 2, 'model': MODEL, 'shape': [.45, .28], 'max_speed': 10., 'gap': 4.5}


def checks():
  return [{'name': category, 'pass': True, 'category': category, 'stage': 'offline'} for category in
          ('response', 'traffic', 'nominal', 'stress', 'independent_response')] + [
            {'name': 'manual', 'pass': True, 'category': 'manual', 'stage': 'release'}]


def test_qualification_is_bound_to_sources_and_coefficients():
  bundle = make_bundle(PROFILE, polynomial_curve(), checks(), hashes={'fixture': '123'})
  assert read_bundle(raw=json.dumps(bundle), hashes={'fixture': '123'})['readiness']['test_ready']
  assert read_bundle(raw=json.dumps(bundle), hashes={'fixture': 'changed'}) is None
  altered = deepcopy(bundle)
  altered['profile']['brake_gain'] *= 1.1
  assert read_bundle(raw=json.dumps(altered), hashes={'fixture': '123'}) is None
  assert not read_bundle(raw=json.dumps(bundle), hashes={'fixture': '123'})['calibration'].validated


def test_physical_evidence_cannot_validate_another_profile_or_skip_missing_checks():
  evidence = {'profile_id': 'a', 'checks': {k: {'pass': True, 'route': '00000022--70dddcd1a2', 'archive_sha256': 'a' * 64} for k in VEHICLE_CHECKS}}
  assert qualification(checks(), 'a', evidence)['road_ready']
  assert not qualification(checks(), 'b', evidence)['road_ready']
  del evidence['checks']['holding']
  assert not qualification(checks(), 'a', evidence)['road_ready']
  assert not qualification([], 'a', evidence)['test_ready']
  failed = checks()
  failed[0]['pass'] = False
  assert not qualification(failed, 'a', evidence)['test_ready']
  assert not qualification(checks()[:1], 'a')['test_ready']


def test_placeholder_physical_evidence_cannot_unlock_normal_driving():
  evidence = {'profile_id': 'a', 'checks': {k: {'pass': True, 'route': 'route', 'archive_sha256': 'hash'} for k in VEHICLE_CHECKS}}
  assert not qualification(checks(), 'a', evidence)['road_ready']


def test_new_evaluation_results_do_not_change_the_tested_controller_identity():
  a = make_bundle(PROFILE, curve(), checks(), hashes={})
  b = make_bundle(PROFILE, curve(), checks() + [{'name': 'new review', 'pass': True, 'stage': 'release'}], hashes={})
  assert a['id'] == b['id']
  assert make_bundle(replace(PROFILE, brake_gain=.01), curve(), checks(), hashes={})['id'] != a['id']


def test_startup_snapshot_reaches_both_control_layers_and_rejects_stale_sources(monkeypatch):
  from types import SimpleNamespace
  import openpilot.common.params as params
  import openpilot.selfdrive.car.volt_profile as profile_module
  from openpilot.selfdrive.controls.lib.longcontrol import LongControl
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner

  monkeypatch.setattr(profile_module, 'source_hashes', lambda: {'fixture': 'current'})
  c = polynomial_curve()
  profile = replace(PROFILE, braking_ki=.3)
  bundle = make_bundle(profile, c, checks())
  raw = json.dumps(bundle)
  monkeypatch.setattr(params, 'Params', lambda: SimpleNamespace(get=lambda *args, **kwargs: raw))
  cp = volt_params()
  assert configure(cp, 'test', profile=profile, test_ready=True) == 'test'
  assert LongControl(cp).volt_profile.braking_ki == .3
  assert tuple(LongControl(cp).volt_stopping.profile.stop_decel) == PROFILE.stop_decel
  assert LongitudinalPlanner(cp).mpc.personal_curve == c
  monkeypatch.setattr(profile_module, 'source_hashes', lambda: {'fixture': 'changed'})
  with pytest.raises(RuntimeError, match='startup snapshot'):
    runtime_bundle(cp)
  configure(cp, 'stock')
  assert runtime_bundle(cp) is None


def test_finite_reference_endpoint_and_deadline_are_not_restarted_by_tracking_error():
  c = curve()
  trajectory = StopTrajectory(c)
  assert trajectory.start(5., 0., 25.)
  finish = trajectory.remaining_time
  assert trajectory.reference[-1, 1] == pytest.approx(25 - c['gap'], abs=.005)
  assert trajectory.reference[-1, 2] == 0.
  for _ in range(20):
    assert trajectory.update(np.array([0., 1., 10.]), 4., -.5, 25. - trajectory.travel, .05) is not None
  assert trajectory.remaining_time == pytest.approx(finish - 1.)
  trajectory.update(np.array([0.]), 4., -.5, 25. - trajectory.travel + .2, .05)
  assert trajectory.remaining_time == pytest.approx(finish - 1.05)
  assert trajectory.update(np.array([0.]), 4., -.5, 10., .05) is None
  assert trajectory.reference is None


def test_infeasible_or_unsupported_stop_does_not_generate_a_comfort_trajectory():
  trajectory = StopTrajectory(curve())
  assert not trajectory.start(20., 0., 40.)
  assert not trajectory.start(5., 0., 5.)
  invalid = curve()
  invalid['deceleration'][1] = float('nan')
  assert not validate_curve(invalid)


def test_unfinished_stop_has_no_finish_timing_or_gap():
  rows = [{'t': float(t), 'v': .1, 'a': -.1, 'gap': 4.5} for t in np.arange(0, 3, .01)]
  result = metrics(rows)
  assert not result['stopped']
  for key in ('stop_time', 'settled_gap', 'low_speed_seconds', 'final_jerk_p95'):
    assert result[key] is None
  assert result['minimum_gap'] == 4.5
  with pytest.raises(ValueError):
    metrics([])


def test_unknown_engine_and_held_telemetry_are_not_fresh_measurements():
  rows = [{'t': float(t), 'v': 5., 'vraw': 5., 'a': -1., 'valid': True, 'active': True, 'foot': False, 'regen': False, 'gas': False,
           'applied_brake': 50., 'applied_gas': -650., 'pressure': 1000., 'sample_times': {'can_368': 0.}}
          for t in np.arange(0., 3., .01)]
  data = prepare(rows)
  assert not data['engine_valid'].any()
  assert not data['pressure_valid'][data['t'] > .3].any()


def test_nonlinear_pressure_reproduction_uses_the_same_kernel_and_bounded_inverse():
  p = {'delay': .1, 'rise': .2, 'release': .05, 'gain': 1., 'gain_low': 1., 'deadband': .05, 'power': 2.}
  f = {'pressure': p, 'regen_delay': 0., 'regen_fade_speed': [0., 8.], 'coefficients': [1., 2., 1., .1, 0.]}
  dynamics = PressureDynamics(f, dt=.05)
  command = np.r_[np.zeros(10), np.ones(30) * .5, np.zeros(30)]
  actual = []
  for brake in command:
    dynamics.step(0., brake * 400, 5.)
    actual.append(dynamics.pressure)
  expected = pressure_trace(command, .05, **p, speed=np.full(len(command), 5.))
  assert actual == pytest.approx(expected)
  assert allocator_profile(f).brake_power == 2.
  known = np.ones(len(command), dtype=bool)
  data = {'t': np.arange(len(command)) * .05, 'v': np.full(len(command), 5.), 'brake': command,
          'pressure': expected, 'physical_a': -2 * expected, 'gas': np.zeros(len(command)), 'regen': np.zeros(len(command)),
          'pitch': np.zeros(len(command)), 'engine': ~known, 'engine_valid': known, 'mask': known,
          'pressure_valid': known, 'pitch_valid': known, 'physical_a_valid': known}
  evaluation = evaluate_pressure_response(data, f)
  assert evaluation['rmse'] == pytest.approx(0., abs=1e-12)
  assert evaluation['low_speed_samples'] == 0 and evaluation['low_speed_rmse'] is None


def test_independent_plant_does_not_recalibrate_the_controller():
  f = {'delay': 0., 'tau': .1, 'speed': [0., 5.], 'coefficients': [1., 3., 0., 1., .1, 0.],
       'pressure_model': {'pressure': {'delay': 0., 'rise': .1, 'release': .1, 'gain': 1., 'gain_low': 1., 'deadband': 0.},
                          'regen_delay': 0., 'regen_fade_speed': [0., 5.], 'coefficients': [1., 2., 1., .1, 0.]}}
  controller = replace(PROFILE, brake_gain=.01)
  plant = VoltPlant(f, speed=5., smooth=True, controller_profile=controller)
  assert plant.controller.volt_profile is controller
  assert plant.long.volt_profile is controller


def test_brake_bundle_needs_no_personal_curve_and_never_sets_personal_flag(monkeypatch):
  from types import SimpleNamespace
  from opendbc.car.gm.volt_longitudinal import VoltFlags
  import openpilot.common.params as params
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  bundle = make_bundle(PROFILE, None, checks(), kind='brake')
  raw = json.dumps(bundle)
  assert read_bundle(raw=raw)['kind'] == 'brake'
  monkeypatch.setattr(params, 'Params', lambda: SimpleNamespace(get=lambda *args, **kwargs: raw))
  cp = volt_params()
  assert configure(cp, 'test', profile=PROFILE, test_ready=True, kind='brake') == 'test'
  assert cp.flags & VoltFlags.BUNDLE and not cp.flags & VoltFlags.PERSONAL
  planner = LongitudinalPlanner(cp)
  assert planner.mpc.personal_curve is None
  assert planner.mpc.stop_distance == 6. and planner.mpc.comfort_brake == 2.5
  assert read_bundle(raw=json.dumps(make_bundle(PROFILE, curve(), checks(), kind='brake'))) is None
  assert configure(cp, 'personal', profile=replace(PROFILE, validated=True, personal_validated=True), kind='brake') == 'stock'
  assert configure(cp, 'smooth', profile=replace(PROFILE, validated=True), kind='brake') == 'smooth'
  monkeypatch.setattr(params, 'Params', lambda: SimpleNamespace(get=lambda *args, **kwargs: None))
  with pytest.raises(RuntimeError, match='startup snapshot'):
    runtime_bundle(cp)
