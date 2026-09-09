from dataclasses import replace
import numpy as np
import pytest
from cereal import car
from opendbc.car.gm.values import CAR, CarControllerParams
from opendbc.car.gm.volt_longitudinal import PROFILE, allocate, configure, enabled, profile_valid
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import volt_params


def test_stock_is_default_and_unvalidated_modes_cannot_enable():
  cp = volt_params()
  assert not enabled(cp)
  assert configure(cp, 'smooth') == 'stock'
  assert configure(cp, 'personal') == 'stock'
  assert configure(cp, 'invalid') == 'stock'
  assert not enabled(cp)


def test_flags_do_not_enable_other_cars_or_camera_topology():
  cp = volt_params(True)
  assert enabled(cp)
  cp.carFingerprint = CAR.CHEVROLET_BOLT_EUV
  assert not enabled(cp)
  cp = volt_params(True)
  cp.networkLocation = car.CarParams.NetworkLocation.fwdCamera
  assert not enabled(cp)


def test_invalid_calibration_is_rejected():
  assert profile_valid()
  assert not profile_valid(replace(PROFILE, brake_gain=float('nan')))
  assert not profile_valid(replace(PROFILE, regen=(1.0,)))
  assert not profile_valid(replace(PROFILE, stop_distance=2.0))


@pytest.mark.parametrize('speed', [0.0, 0.1, 0.5, 1.0, 1.5, 2.0, 5.0, 10.0, 20.0, 35.0])
@pytest.mark.parametrize('engine', [False, True, None])
def test_brake_allocation_is_bounded_and_monotonic(speed, engine):
  p = CarControllerParams(volt_params())
  commands = [allocate(a, speed, p, engine_running=engine) for a in np.linspace(-5, 3, 401)]
  assert all(-650 <= gas <= 1018 and 0 <= brake <= 400 for gas, brake in commands)
  assert all(a[0] <= b[0] and a[1] >= b[1] for a, b in zip(commands, commands[1:], strict=False))


def test_effective_friction_braking_replaces_low_speed_regen_deadzone():
  p = CarControllerParams(volt_params())
  assert np.interp(-0.5, p.BRAKE_LOOKUP_BP, p.BRAKE_LOOKUP_V) == 0
  gas, brake = allocate(-0.5, 0.6, p, stopping=True, engine_running=False)
  assert gas == -650 and brake > 0
  assert abs(allocate(-0.5, 0.999, p)[1] - allocate(-0.5, 1.001, p)[1]) <= 1


def test_braking_integral_does_not_run_to_minus_four_during_delayed_response():
  cp = volt_params(True)
  lc = LongControl(cp)
  cs = car.CarState.new_message(vEgo=15.0, aEgo=0.0)
  values = [lc.update(True, cs, -2.7, False, (-4.0, 2.0)) for _ in range(100)]
  assert min(values) >= -2.7 - PROFILE.integral_limit - 1e-6
  assert abs(lc.pid.i) <= PROFILE.integral_limit
  # An urgent planner request retains the original -4 m/s² command authority.
  assert lc.update(True, cs, -4.0, False, (-4.0, 2.0)) == -4


def test_stopping_exit_and_disengagement_reset():
  cp = volt_params(True)
  lc = LongControl(cp)
  cs = car.CarState.new_message(vEgo=0.6, aEgo=-0.4)
  for _ in range(100):
    lc.update(True, cs, -0.2, True, (-4.0, 2.0))
  assert lc.long_control_state == LongCtrlState.stopping
  value = lc.update(True, cs, 0.2, False, (-4.0, 2.0))
  assert lc.long_control_state == LongCtrlState.pid and np.isfinite(value)
  assert lc.update(False, cs, 0.0, False, (-4.0, 2.0)) == 0
  assert lc.pid.i == 0


def test_offline_taper_is_per_instance_and_retains_urgent_braking():
  from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping

  baseline = LongControl(volt_params(True))
  experiment = LongControl(volt_params(True))
  experiment.volt_stopping = VoltStopping(replace(PROFILE, stop_decel=(0.18, 0.9, 1.1)))
  cs = car.CarState.new_message(vEgo=0.5, aEgo=-0.4)
  for _ in range(100):
    original = baseline.update(True, cs, -0.1, True, (-4.0, 2.0))
    changed = experiment.update(True, cs, -0.1, True, (-4.0, 2.0))
  assert changed < original - 0.3
  assert baseline.volt_stopping.profile is PROFILE
  assert experiment.update(True, cs, -4.0, True, (-4.0, 2.0)) == -4


@pytest.mark.parametrize('should_stop', [False, True])
def test_positive_integral_cannot_soften_full_braking_request(should_stop):
  lc = LongControl(volt_params(True))
  cs = car.CarState.new_message(vEgo=0.5, aEgo=-0.4)
  lc.update(True, cs, -0.1, should_stop, (-4.0, 2.0))
  lc.pid.i = 0.4
  assert lc.update(True, cs, -4.0, should_stop, (-4.0, 2.0)) == -4
  assert lc.update(False, cs, -4.0, should_stop, (-4.0, 2.0)) == 0


def test_stationary_noise_does_not_ratchet_holding_brake():
  cp = volt_params(True)
  lc = LongControl(cp)
  cs = car.CarState.new_message(vEgo=0.0, standstill=True)
  values = []
  for k in range(1000):
    cs.aEgo = float(np.sin(k) * 0.2)
    values.append(lc.update(True, cs, 0.0, True, (-4.0, 2.0)))
  assert max(values[100:]) - min(values[100:]) < 1e-6
  gas, brake = allocate(values[-1], 0.0, CarControllerParams(cp), stopping=True, standstill=True)
  assert gas == -650 and brake >= 133


def test_standstill_flag_cannot_weaken_full_braking_while_wheels_move():
  controller = LongControl(volt_params(True))
  cs = car.CarState.new_message(vEgo=.05, vEgoRaw=.05, standstill=True)
  for _ in range(25):
    controller.update(True, cs, -.2, True, (-4., 2.))
  assert controller.update(True, cs, -4., True, (-4., 2.)) == -4.
  assert controller.update(False, cs, -4., True, (-4., 2.)) == 0.


def test_carried_positive_integral_cannot_cancel_the_final_taper():
  lc = LongControl(volt_params(True))
  cs = car.CarState.new_message(vEgo=0.2, aEgo=-0.3)
  lc.update(True, cs, -0.3, True, (-4.0, 2.0))
  lc.pid.i = PROFILE.integral_limit
  result = lc.update(True, cs, -0.3, True, (-4.0, 2.0))
  assert result < -0.15
  assert lc.pid.i <= PROFILE.integral_limit * cs.vEgo / 2


@pytest.mark.parametrize('pitch', [-0.1, -0.05, 0.0, 0.05, 0.1, float('nan')])
def test_grade_feedforward_is_finite_bounded_and_continuous(pitch):
  params = CarControllerParams(volt_params())
  commands = [allocate(float(a), 0.5, params, pitch=pitch) for a in np.linspace(-4, 2, 1201)]
  assert all(np.isfinite(g) and -650 <= g <= 1018 and 0 <= b <= 400 for g, b in commands)
  assert all(a[0] <= b[0] and a[1] >= b[1] for a, b in zip(commands, commands[1:], strict=False))


def test_startup_config_keeps_same_controller_params_and_stock_when_unvalidated():
  from opendbc.car.gm.interface import CarInterface

  cp = volt_params()
  ci = CarInterface(cp)
  assert ci.CC.CP is cp
  assert configure(cp, 'smooth') == 'stock'
  assert not enabled(ci.CC.CP)


def test_personal_planner_parameters_are_per_instance_and_stock_defaults_survive():
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
  from cereal import log

  custom = LongitudinalMpc(stop_distance=4.5, comfort_brake=1.5, jerk_scale=1.5)
  stock = LongitudinalMpc()
  radar = log.RadarState.new_message()
  radar.leadOne.status = True
  radar.leadOne.dRel = 50.0
  radar.leadOne.vLead = 0.0
  radar.leadOne.aLeadTau = 1.5
  for mpc in (custom, stock):
    mpc.set_cur_state(10.0, 0.0)
    mpc.update(radar, 10.0)
    assert mpc.solution_status == 0
    assert np.all(mpc.params[:, 5] == 0.75)
  assert np.all(custom.params[:, 6] == 4.5) and np.all(custom.params[:, 7] == 1.5)
  assert np.all(stock.params[:, 6] == 6.0) and np.all(stock.params[:, 7] == 2.5)


def test_stopping_entry_is_continuous_despite_measured_response_lag():
  lc = LongControl(volt_params(True))
  lc.long_control_state = LongCtrlState.pid
  lc.last_output_accel = -.8
  lc.pid.i = -.3
  cs = car.CarState.new_message(vEgo=.4, aEgo=-.1)
  output = lc.update(True, cs, -.6, True, (-4., 2.))
  assert abs(output - -.8) < .03


def test_final_release_does_not_keep_a_stale_strong_brake_target():
  lc = LongControl(volt_params(True))
  lc.long_control_state = LongCtrlState.stopping
  lc.volt_stopping.target = -.9
  lc.last_output_accel = -.9
  cs = car.CarState.new_message(vEgo=.2, aEgo=-1.)
  for _ in range(40):
    output = lc.update(True, cs, 0., True, (-4., 2.))
  assert -.35 < output <= 0.


@pytest.mark.parametrize('invalidate', ['stale', 'moving', 'identity', 'secondary', 'override'])
def test_stationary_lead_latch_holds_through_noise_and_releases(invalidate):
  from cereal import log
  from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopLatch
  radar = log.RadarState.new_message()
  lead = radar.leadOne
  lead.status, lead.radar, lead.radarTrackId, lead.modelProb, lead.dRel = True, True, 1, 1., 6.
  latch = VoltStopLatch(.05)
  for _ in range(12):
    held = latch.update(lead, radar.leadTwo, .05, True, .4, False, True, .5)
  assert held
  assert latch.update(lead, radar.leadTwo, .05, False, .1, False, True, .5)
  if invalidate == 'moving':
    lead.vLead = 1.
  if invalidate == 'identity':
    lead.radarTrackId = 2
  if invalidate == 'secondary':
    radar.leadTwo.status, radar.leadTwo.dRel = True, 3.
  assert not latch.update(lead, radar.leadTwo, .31 if invalidate == 'stale' else .05, False, .1, False, invalidate != 'override', .5)
