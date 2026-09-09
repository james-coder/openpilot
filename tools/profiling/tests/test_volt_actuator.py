from types import SimpleNamespace
import numpy as np
import pytest

from openpilot.tools.profiling.volt_actuator import DelayedResponse, friction_motion
from openpilot.tools.profiling.volt_response_fit import prepare, combine_prepared
from openpilot.tools.profiling.volt_pressure_model import episode_pressure, PressureDynamics, response_features
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_replay import environment
from openpilot.tools.profiling.volt_finish_metrics import physical_finish


@pytest.mark.parametrize('dt', [.01, .02, .05])
def test_fractional_delay_has_the_same_continuous_time_response(dt):
  lag = DelayedResponse(.035, .2, .07)
  for k in range(round(1. / dt)):
    lag.step(0. if k * dt < .1 - 1e-9 else 1., dt)
  assert lag.value == pytest.approx(1 - np.exp(-(.865) / .2), abs=1e-12)
  value = lag.value
  for _ in range(round(.5 / dt)):
    lag.step(0., dt)
  peak = 1 + (value - 1) * np.exp(-.035 / .2)
  assert lag.value == pytest.approx(peak * np.exp(-.465 / .07), abs=1e-12)


def test_delayed_response_requires_real_preceding_history():
  lag = DelayedResponse(.2, .1)
  with pytest.raises(ValueError, match='history'):
    lag.seed(1., [(1., 0.)], .5)
  lag.seed(1., [(.7, 0.), (.9, 1.)], .5)
  assert lag.step(0., .1) == pytest.approx(.5 * np.exp(-1.))
  assert lag.step(0., .1) > .6


@pytest.mark.parametrize('drive', [-.5, .5])
def test_static_friction_holds_and_insufficient_pressure_allows_motion(drive):
  assert friction_motion(0., drive, .6, .01) == (0., 0., 0.)
  speed, distance, accel = friction_motion(0., drive, .2, .01)
  assert accel == pytest.approx(np.sign(drive) * .3)
  assert np.sign(speed) == np.sign(distance) == np.sign(drive)


def test_friction_does_not_propel_a_car_through_zero_or_reverse_its_sign():
  assert friction_motion(.001, 0., 2., .01)[0] == 0.
  assert friction_motion(-.001, 0., 2., .01)[0] == 0.
  assert friction_motion(-1., 0., 2., .01)[0] == pytest.approx(-.98)


def rows_fixture():
  return [dict(t=float(t), v=1., vraw=1., a=-.2, pressure=1000., applied_brake=30., applied_gas=-650.,
               foot=False, pedal=0., gas=False, regen=False, active=True, valid=True, steering_angle=0.,
               controller_pitch=.02, engine_rpm=0., brake_mode=10, vehicle_ax=-.2, regen_raw=0.)
          for t in np.arange(0., 12., .05)]


@pytest.mark.parametrize('pedal,foot,accepted', [(0., False, True), (1., False, True), (7., False, True),
                                               (8., False, False), (0., True, False), (np.nan, False, False)])
def test_gateway_pedal_mask_matches_decoded_brake_threshold(pedal, foot, accepted):
  from openpilot.tools.profiling.volt_observations import sample_rows, autonomous_mask
  from openpilot.tools.profiling.volt_brake_diagnostics import masks
  rows = rows_fixture()
  for row in rows:
    row.update(pedal=pedal, foot=foot)
  data = sample_rows(rows, np.array([r['t'] for r in rows]))
  assert bool(autonomous_mask(data, .05).all()) == accepted
  np.testing.assert_array_equal(masks(data)[0], autonomous_mask(data, .05))


def test_gaps_and_interventions_break_filter_state_and_pooling_keeps_boundaries():
  rows = rows_fixture()
  for row in rows:
    if 4 <= row['t'] < 5:
      row['active'] = False
      row['foot'] = True
      row['applied_brake'] = 400.
  data = prepare(rows)
  assert not data['mask'][(data['t'] > 3) & (data['t'] < 7.2)].any()
  assert len(set(data['episode'][data['mask']])) == 2
  both = combine_prepared([data, data])
  assert len(both['t']) == 2 * len(data['t'])
  assert len(set(both['episode'][both['mask']])) == 4
  p = dict(delay=.2, rise=.1, release=.1, gain=1., deadband=0.)
  predicted = episode_pressure(data, p)
  for row in rows:
    if 4 <= row['t'] < 5:
      row['applied_brake'] = 0.
  np.testing.assert_equal(predicted, episode_pressure(prepare(rows), p))


def test_measurements_never_interpolate_future_values_or_default_unknowns():
  rows = rows_fixture()
  for row in rows:
    row['pressure'] = 1000. if row['t'] < 2. else 10000.
    row['sample_times'] = {'can_368': 0. if row['t'] < 2. else 2.}
    del row['engine_rpm']
  data = prepare(rows)
  assert np.isnan(data['pressure'][(data['t'] > .31) & (data['t'] < 2.)]).all()
  assert not data['engine_valid'].any()


def test_replay_environment_reaches_both_physics_and_controller():
  plant = SimpleNamespace(cs=SimpleNamespace(), cc=SimpleNamespace())
  environment(plant, {'controller_pitch': .05, 'engine_rpm': 1000.})
  assert plant.grade == pytest.approx(np.tan(.05))
  assert plant.cc.orientationNED[1] == .05 and plant.cs.volt_engine_running
  with pytest.raises(ValueError, match='engine'):
    environment(plant, {'controller_pitch': .05})


def test_reverse_motion_cannot_be_reported_as_a_confirmed_stop():
  trace = [dict(t=float(t), physical_v=-.2, physical_accel=0., standstill=False) for t in np.arange(0., 2., .01)]
  assert physical_finish(trace)['confirmed_stop'] is None


def test_holding_capacity_is_not_terminal_kinematic_jerk():
  trace = []
  for t in np.arange(0., 2., .01):
    v = max(0., 1-t) ** 4
    trace.append(dict(t=float(t), physical_v=v, physical_accel=-4 * max(0., 1-t) ** 3,
                      force_accel=-3. if t > 1 else 0., standstill=v <= .0864))
  result = physical_finish(trace)
  assert result['confirmed_stop'] is not None
  assert result['rollback_distance'] == 0.
  # Changing a static holding force cannot change motion-derived metrics.
  for row in trace:
    row['force_accel'] = -100.
  assert physical_finish(trace) == result


def test_joint_command_response_matches_identification_without_measured_regen():
  rows = rows_fixture()
  for row in rows:
    row['vraw'] = row['v'] = 5.
    row['applied_brake'] = 40. if row['t'] < 3 else 100.
    row['pressure'] = 0.
  data = prepare(rows)
  fit = {'pressure': dict(delay=.1, rise=.2, release=.1, gain=1., deadband=0.),
         'regen_delay': .2, 'regen_fade_speed': [0., 8.], 'coefficients': [1., 2., .5, .1, 0., 1.]}
  plant = PressureDynamics(fit, dt=.05)
  actual = [0.]
  for i in range(len(data['t']) - 1):
    accel = plant.step(data['gas'][i] * 1018 - data['regen'][i] * 650, data['brake'][i] * 400, 5., .02)
    actual.append(accel + fit['coefficients'][1] * plant.pressure)
  X = response_features(data, fit['regen_delay'], *fit['regen_fade_speed'])
  predicted = X @ fit['coefficients'] - 9.81 * np.sin(.02)
  mask = data['mask'] & (data['t'] > 2.)
  assert np.array(actual)[mask] == pytest.approx(predicted[mask], abs=1e-12)
