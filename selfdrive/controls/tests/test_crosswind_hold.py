import json
import math

import numpy as np
import pytest

from cereal import car, log
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib import crosswind_hold as ch
from openpilot.selfdrive.controls.lib.latcontrol_pid import HOLD_I_LIMIT, LatControlPID

NOW = 1_000_000.


def status(cross, age=5., reading_age=600.):
  return {'time': NOW - age, 'reading_time': NOW - reading_age, 'cross_mph': cross}


def test_crosswind_needs_fresh_daemon_and_reading():
  assert ch.crosswind_mph(status(-15.), NOW) == 15.
  assert ch.crosswind_mph(status(15., age=60.), NOW) is None            # windd not running
  assert ch.crosswind_mph(status(15., reading_age=3600.), NOW) is None  # forecast too old
  assert ch.crosswind_mph(status(None), NOW) is None                   # stopped / no heading
  assert ch.crosswind_mph({}, NOW) is None
  assert ch.crosswind_mph({'time': 'x', 'reading_time': NOW, 'cross_mph': 20}, NOW) is None


def test_hysteresis():
  assert not ch.next_active(11.9, False)
  assert ch.next_active(12., False)
  assert ch.next_active(8.5, True)
  assert not ch.next_active(7.9, True)
  assert not ch.next_active(None, True)


def test_setting_off_never_activates(tmp_path):
  (tmp_path / 'status.json').write_text(json.dumps({'time': NOW, 'reading_time': NOW, 'cross_mph': 30}))
  hold = ch.CrosswindHold(enabled=False, root=tmp_path, start_thread=False)
  hold.refresh(NOW)
  assert not hold.active
  hold = ch.CrosswindHold(enabled=True, root=tmp_path, start_thread=False)
  hold.refresh(NOW)
  assert hold.active


def controller(active_hold):
  CP = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  CI = CarInterface(CP)
  lac = LatControlPID(CP.as_reader(), CI, DT_CTRL)
  lac.hold = ch.CrosswindHold(enabled=True, start_thread=False)
  lac.hold.active = active_hold
  return lac, VehicleModel(CP)


def drive(lac, VM, d=0.15, v=29., T=30., press=None, hold_off_at=None):
  """Toy loop: first-order steering + kinematic car + model-like lane centering, constant side push d."""
  CS = car.CarState.new_message(vEgo=v)
  params = log.LiveParametersData.new_message()
  theta = y = psi = 0.
  queue = [0.] * 20
  out = []
  for k in range(int(T / DT_CTRL)):
    t = k * DT_CTRL
    if hold_off_at is not None and t >= hold_off_at:
      lac.hold.active = False
    kd = -(0.0009 * y + 0.004 * v * psi)
    CS.steeringAngleDeg = float(theta)
    CS.steeringPressed = bool(press and press[0] <= t < press[1])
    u, _, _ = lac.update(True, CS, VM, params, False, kd, False, 0.2)
    queue.append(float(u))
    theta += (10.7 * (queue.pop(0) + d) - theta) / 0.3 * DT_CTRL
    psi += v * -VM.calc_curvature(math.radians(theta), v, 0.) * DT_CTRL
    y += v * psi * DT_CTRL
    out.append((t, y, lac.pid.i, float(u)))
  return np.array(out)


def test_inactive_hold_is_exactly_stock():
  held, VM = controller(False)
  CP = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  stock = LatControlPID(CP.as_reader(), CarInterface(CP), DT_CTRL)
  stock.hold = None
  a, b = drive(held, VM), drive(stock, VM)
  assert np.array_equal(a[:, 3], b[:, 3])
  assert not a[:, 2].any()


def test_hold_removes_steady_offset_within_cap():
  off = drive(*controller(False))[-1, 1]
  on = drive(*controller(True))
  assert abs(off) > .05                 # the stock P-only loop settles off-center
  assert abs(on[-1, 1]) < abs(off) / 5  # the hold brings it back
  assert np.abs(on[:, 2]).max() <= HOLD_I_LIMIT + 1e-9


def test_strong_push_is_capped():
  on = drive(*controller(True), d=0.8)
  assert np.abs(on[:, 2]).max() == pytest.approx(HOLD_I_LIMIT)


def test_hold_fades_out_when_wind_drops():
  o = drive(*controller(True), T=40., hold_off_at=30.)
  i30 = abs(o[2999, 2])
  assert i30 > .05
  assert abs(o[3299, 2]) < i30 * .3   # mostly gone 3 s later
  assert abs(o[-1, 2]) < .01


def test_driver_override_clears_hold_and_disengage_resets():
  lac, VM = controller(True)
  o = drive(lac, VM, T=25., press=(20., 21.5))
  assert abs(o[1999, 2]) > .05
  assert abs(o[2140, 2]) == 0.         # held >1 s by the driver: cleared
  lac.reset()
  assert lac.pid.i == 0.


def test_firmer_steering_scales_gains_and_stays_stable():
  from openpilot.selfdrive.controls.lib.latcontrol_pid import FIRMER_FF_SCALE, FIRMER_KP_SCALE
  CP = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  lac, VM = controller(False)
  kp0, ff0 = lac.pid.k_p, lac.ff_factor
  lac.set_firmer(CP)
  lac.pid.speed = 30.
  assert lac.pid.k_p == pytest.approx(np.interp(30., CP.lateralTuning.pid.kpBP, CP.lateralTuning.pid.kpV) * FIRMER_KP_SCALE)
  assert lac.ff_factor == pytest.approx(ff0 * FIRMER_FF_SCALE) and kp0 is not None
  o = drive(lac, VM, d=0.1, T=40.)
  y = o[2000:, 1]
  assert np.ptp(y) < .05  # settles; no sustained weave in the toy loop
