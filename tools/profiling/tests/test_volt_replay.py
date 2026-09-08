from copy import deepcopy
import numpy as np
import pytest
import zstandard
from cereal import log
from openpilot.selfdrive.locationd.helpers import PoseCalibrator
from openpilot.tools.profiling.volt_braking import calibrated_motion, extract_native, export_review
from openpilot.tools.profiling.volt_pressure_model import hold_sample, pressure_trace, PressureDynamics
from openpilot.tools.profiling.volt_response_fit import approach_curve, study_split
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_replay import traffic_window, at, replay_commands
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc


def curve_fixture():
  examples = [{'id': 'train', 'route': 'old', 'gap': 3.5}, {'id': 'test', 'route': 'new', 'gap': 3.25}]
  samples = {e['id']: [{'v': float(v), 'a': -0.8} for v in np.linspace(.3, 10, 2000)] for e in examples}
  return examples, samples, {'holdout_route': 'new'}


def test_causal_hold_never_uses_future_command():
  assert np.isnan(hold_sample([1., 2.], [10., 20.], [0.])[0])
  assert list(hold_sample([1., 2.], [10., 20.], [1., 1.99, 2.])) == [10., 10., 20.]
  with pytest.raises(ValueError, match='stale'):
    at((np.array([1.]), [{}]), 1.31)


def test_command_replay_trims_an_initial_incomplete_window():
  fit = {'delay': 0., 'tau': .1, 'speed': [0., 5.], 'coefficients': [1., 3., 0., 1., .1, 0.]}
  rows = []
  for t in np.arange(0., .2, .01):
    row = {'t': float(t), 'vraw': 1., 'v': 1., 'a': 0., 'valid': True, 'active': True, 'foot': False, 'regen': False, 'gas': False}
    if t >= .03:
      row.update(applied_gas=0., applied_brake=0.)
    rows.append(row)
  result = replay_commands({'samples': rows}, fit)
  assert result[0]['t'] == pytest.approx(.03)


def test_pressure_simulation_matches_fitting_kernel():
  p = {'delay': .1, 'rise': .2, 'release': .05, 'gain': 1., 'gain_low': 1., 'deadband': .05}
  f = {'pressure': p, 'regen_delay': 0., 'regen_fade_speed': [.5, 5.], 'coefficients': [1., 1., 1., 0., 0.]}
  dynamics = PressureDynamics(f, dt=.05)
  command = np.r_[np.zeros(10), np.ones(30) * .5, np.zeros(30)]
  pressures = []
  for brake in command:
    dynamics.step(0., brake * 400, 0.)
    pressures.append(dynamics.pressure)
  expected = pressure_trace(command, .05, p['delay'], p['rise'], p['release'], p['gain'], p['deadband'], p['gain_low'])
  assert pressures == pytest.approx(expected)
  assert pressures[-1] < .001


def test_device_mount_pitch_is_not_vehicle_grade():
  calibrator = PoseCalibrator()
  calib = log.LiveCalibrationData.new_message(calStatus='calibrated', rpyCalib=[0., .15, 0.])
  calibrator.feed_live_calib(calib)
  pose = log.LivePose.new_message(inputsOK=True, sensorsOK=True)
  pose.orientationNED.y, pose.orientationNED.valid = -.15, True
  pose.accelerationDevice.x, pose.accelerationDevice.valid = 1., True
  result = calibrated_motion(pose, calibrator)
  assert result['device_pitch'] == pytest.approx(-.15)
  assert result['vehicle_pitch'] == pytest.approx(0., abs=1e-7)
  assert result['vehicle_ax'] == pytest.approx(np.cos(.15))
  calibrator.calib_valid = False
  assert 'vehicle_pitch' not in calibrated_motion(pose, calibrator)


def test_native_tracks_are_attached_only_while_fresh(tmp_path):
  messages = []
  for kind, t in [('liveTracks', 1.), ('radarState', 1.01), ('carState', 1.05), ('radarState', 1.4), ('carState', 1.45)]:
    e = log.Event.new_message(logMonoTime=int(t * 1e9), valid=True)
    r = e.init(kind)
    if kind == 'liveTracks':
      points = r.init('points', 1)
      points[0].trackId, points[0].dRel = 7, 12.
    elif kind == 'radarState':
      r.leadOne.status, r.leadOne.radarTrackId = True, 7
    messages.append(e.to_bytes())
  segment = tmp_path / '00000001--0123456789--0'
  segment.mkdir()
  path = segment / 'rlog.zst'
  path.write_bytes(zstandard.ZstdCompressor().compress(b''.join(messages)))
  rows = extract_native(path)['rows']
  assert rows[0]['raw_track']['id'] == 7
  assert rows[0]['sample_times']['radarState'] == pytest.approx(1.01)
  assert 'raw_track' not in rows[1]


def test_approach_fit_never_trains_on_reserved_route():
  examples, samples, split = curve_fixture()
  first = approach_curve(examples, samples, split)
  for row in samples['test']:
    row['a'] = -2.
  second = approach_curve(examples, samples, split)
  assert first['coefficients'] == second['coefficients']
  assert first['gap'] == second['gap'] == 4.5
  assert second['evaluation_rmse'] > first['evaluation_rmse'] + 1
  assert not second['supported_for_release']


def test_new_route_is_reserved_before_fit_and_never_replaced(tmp_path):
  def index(route):
    return {'events': [{'route': route, 'recommended_manual': True} for _ in range(3)]}
  assert study_split(tmp_path, index('old'))['holdout_route'] is None
  assert study_split(tmp_path, index('new'))['holdout_route'] == 'new'
  assert study_split(tmp_path, index('another'))['holdout_route'] == 'new'


def test_empty_archive_does_not_replace_existing_review(tmp_path):
  marker = tmp_path / 'index.json'
  marker.write_text('{"events":[]}')
  with pytest.raises(ValueError, match='No rlogs'):
    export_review(tmp_path / 'missing', tmp_path, tmp_path / 'cache')
  assert marker.read_text() == '{"events":[]}'


def test_personal_mpc_rejects_stale_moving_and_changed_leads():
  mpc = LongitudinalMpc()
  curve = approach_curve(*curve_fixture())
  mpc.set_personal_curve(curve)
  radar = log.RadarState.new_message()
  lead = radar.leadOne
  lead.status, lead.radar, lead.radarTrackId, lead.modelProb = True, True, 1, 1.
  for _ in range(30):
    mpc.update_personal(radar, .05)
  assert mpc.personal_blend == 1.
  mpc.update_personal(radar, .31)
  assert mpc.personal_blend == 0.
  for _ in range(30):
    mpc.update_personal(radar, .05)
  lead.vLead = 1.
  mpc.update_personal(radar, 0.)
  assert mpc.personal_blend == 0.
  lead.vLead, lead.radarTrackId = 0., 2
  mpc.update_personal(radar, 0.)
  assert mpc.personal_blend == 0.
  invalid = deepcopy(curve)
  invalid['coefficients'][1][3] += 1.
  with pytest.raises(ValueError, match='continuous'):
    mpc.set_personal_curve(invalid)


def test_stock_planner_does_not_require_new_replay_metadata():
  import cereal.messaging as messaging
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import volt_params

  messages = {name: getattr(messaging.new_message(name), name) for name in
              ('carState', 'controlsState', 'selfdriveState', 'liveParameters', 'carControl', 'modelV2', 'radarState')}
  class Messages:
    def __getitem__(self, key):
      return messages[key]
  planner = LongitudinalPlanner(volt_params())
  planner.update(Messages())
  assert planner.mpc.personal_blend == 0.


def test_traffic_window_refuses_ambiguous_final_identity():
  rows = [{'t': float(t), 'valid': True, 'radar_valid': True, 'lead': True,
           'leads': [{'status': True, 'radar': True, 'radarTrackId': 1 if t < -1 else 2}]} for t in np.arange(-12., 2., .01)]
  with pytest.raises(ValueError, match='identity'):
    traffic_window({'samples': rows})
