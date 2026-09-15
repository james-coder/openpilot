import json
from types import SimpleNamespace
import numpy as np
import pytest
from numpy.polynomial import polynomial as poly

from openpilot.selfdrive.controls.lib.volt_polynomial import generate, PolynomialStopTrajectory, MODEL
from openpilot.selfdrive.car.volt_profile import make_bundle, read_bundle
from openpilot.tools.profiling.volt_finish_metrics import physical_finish
from openpilot.tools.profiling.volt_function_fit import manual_windows
from opendbc.car.gm.volt_longitudinal import PROFILE


def model():
  return {'version': 2, 'model': MODEL, 'shape': [.45, .28], 'max_speed': 10., 'gap': 4.5}


@pytest.mark.parametrize('v,a,d', [(5., 0., 20.5), (10., 0., 50.5), (5., -1., 12.)])
def test_integrated_curve_matches_boundary_state_and_distance(v, a, d):
  r = generate(v, a, d, model()['shape'])
  assert r is not None
  t = np.linspace(0., r.duration, 10001)
  values = r.evaluate(t)
  assert values[0, 1:] == pytest.approx([v, a, 0.], abs=1e-8)
  # Check the actual polynomials, independently of endpoint output handling.
  assert [poly.polyval(1., c) for c in (r.v, r.a, r.j)] == pytest.approx([0., 0., 0.], abs=1e-8)
  assert np.trapezoid(values[:, 1], t) == pytest.approx(d, abs=1e-5)
  assert values[-1, 0] == pytest.approx(d, abs=1e-8)
  assert np.min(values[:, 1]) >= -1e-8 and np.max(np.diff(values[:, 1])) <= 1e-8
  near = values[t >= r.duration-.1]
  assert max(abs(near[:, 2])) <= .05+1e-6 and max(abs(near[:, 3])) <= .3+1e-6


def test_infeasibility_and_range_change_return_control_to_normal_planner():
  assert generate(10., 0., 1., model()['shape']) is None
  assert generate(5., -3., 20., model()['shape']) is None
  assert generate(float('nan'), 0., 20., model()['shape']) is None
  trajectory = PolynomialStopTrajectory(model())
  assert trajectory.start(5., 0., 25.)
  duration = trajectory.remaining_time
  assert trajectory.update([0., 1.], 4., -.5, 25., .05) is not None
  assert trajectory.remaining_time == pytest.approx(duration-.05)
  assert trajectory.update([0.], 4., -.5, 20., .05) is None
  assert trajectory.reference is None
  assert not trajectory.start(5., .2, 25.)  # Do not silently replace entry acceleration.


def test_old_personal_bundle_and_invalid_function_cannot_activate():
  bundle = make_bundle(PROFILE, model(), [], hashes={})
  assert read_bundle(raw=json.dumps(bundle), hashes={}) is not None
  old = make_bundle(PROFILE, {'version': 1}, [], hashes={})
  assert read_bundle(raw=json.dumps(old), hashes={}) is None
  bad = make_bundle(PROFILE, {**model(), 'shape': [.2, .8]}, [], hashes={})
  assert read_bundle(raw=json.dumps(bad), hashes={}) is None
  bundle['version'] = 2
  assert read_bundle(raw=json.dumps(bundle), hashes={}) is None


def test_trajectory_tracking_removes_the_legacy_minimum_braking_floor():
  from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping
  from openpilot.common.pid import PIDController
  state = SimpleNamespace(vEgo=.2, vEgoRaw=.2, aEgo=-.02, standstill=False)
  def run(active):
    stop = VoltStopping()
    pid = PIDController(0., .35, rate=100, pos_limit=2., neg_limit=-4.)
    return stop.update(state, -.02, True, -.02, pid, trajectory_active=active)
  assert run(True) == pytest.approx(-.02)
  assert run(False) < run(True)


def test_physical_jerk_check_cannot_be_satisfied_by_clamping_speed_to_zero():
  trace = [{'t': float(t), 'physical_v': max(0., 1.-t), 'physical_accel': -2. if t > .7 else -1.}
           for t in np.arange(0., 2., .01)]
  result = physical_finish(trace)
  assert result['confirmed_stop'] is not None
  assert result['terminal_accel'] == 2.
  assert result['terminal_jerk_p95'] > .5


def test_manual_fit_does_not_invent_standstill_from_legacy_event_marker():
  rows = [{'t': float(t), 'v': .2, 'vraw': .2, 'standstill': False, 'active': False, 'valid': True, 'foot': True, 'gas': False}
          for t in np.arange(-3., 2., .01)]
  windows, reason = manual_windows({'samples': rows})
  assert windows == [] and reason == 'no confirmed wheel standstill'


def test_polynomial_mpc_handoff_restores_stock_gap_when_radar_is_stale():
  from cereal import log
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
  mpc = LongitudinalMpc()
  mpc.set_personal_curve(model())
  mpc.set_cur_state(5., 0.)
  radar = log.RadarState.new_message()
  radar.leadOne.status = radar.leadOne.radar = True
  radar.leadOne.radarTrackId = 1
  radar.leadOne.modelProb = 1.
  radar.leadOne.dRel = 25.
  radar.leadOne.aLeadTau = 1.5
  for _ in range(12):
    mpc.update_personal(radar, 0.)
  mpc.update(radar, 5., radar_age=0., measured_state=(5., 0.))
  assert mpc.polynomial_active
  assert np.all(mpc.params[:, 6] == 4.5)
  assert np.all(mpc.params[:, 8:] == 0.)  # Retired lookup cannot fight the function.
  mpc.update(radar, 5., radar_age=1., measured_state=(5., 0.))
  assert not mpc.polynomial_active
  assert np.all(mpc.params[:, 6] == 6.)
