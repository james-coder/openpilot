import json

import numpy as np
import pytest

from cereal import car, log
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib import steer_experiment as se
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID

NOW = 1_000_000.


def cfg(**over):
  d = {'B': {'friction': 0.05, 'kp': 1.0, 'ff': 1.0}, 'blocks': 4, 'block_s': 30, 'seed': 7, 'armed_at': NOW - 60}
  d.update(over)
  return d


class Clock:
  def __init__(self):
    self.t = 100.

  def __call__(self):
    return self.t


def make(clock, **over):
  parsed = se.parse_config(cfg(**over), wall_now=NOW)
  return se.SteerExperiment(parsed, root=over.pop('root', se.ROOT), mono=clock, wall=lambda: NOW, start_thread=False)


def drive(exp, clock, seconds, active=True, v=15.6, a=0., pressed=False, curv=0.):
  mixes = []
  for _ in range(int(seconds / .01)):
    clock.t += .01
    mixes.append(exp.step(active, v, a, pressed, curv))
  return mixes


def test_config_is_validated_and_clamped():
  assert se.parse_config(None) is None
  assert se.parse_config('not json') is None
  assert se.parse_config(json.dumps({'B': {}})) is None                                   # never armed
  assert se.parse_config(cfg(armed_at=NOW - se.ARM_MAX_AGE_S - 1), wall_now=NOW) is None  # stale arming
  assert se.parse_config(cfg(armed_at=NOW + 100), wall_now=NOW) is None                    # from the future
  c = se.parse_config(cfg(B={'friction': 5., 'kp': 9., 'ff': -3.}, blocks=99, block_s=1, min_mph=1, max_mph=500), wall_now=NOW)
  assert c['friction'] == se.FRICTION_MAX and c['kp'] == se.KP_RANGE[1] and c['ff'] == se.FF_RANGE[0]
  assert c['blocks'] == 20 and c['block_s'] == 30 and c['min_mph'] == 15 and c['max_mph'] == 80
  assert se.parse_config(cfg(blocks=5), wall_now=NOW)['blocks'] == 6                       # always whole A/B pairs


def test_friction_assist_shape_and_cap():
  f = se.friction_assist
  assert f(0.05, .05) == 0 and f(-0.1, .05) == 0                    # inside the sensor-noise deadband
  assert f(0.35, .05) == pytest.approx(.05 * .5)                    # halfway up the ramp
  assert f(2., .05) == pytest.approx(.05) and f(-2., .05) == pytest.approx(-.05)
  assert f(2., 1.0) == se.FRICTION_MAX                              # the cap holds even if asked for more
  assert f(float('nan'), .05) == 0 and f(1., 0.) == 0
  xs = np.linspace(0, 2, 50)
  assert all(f(-x, .05) == -f(x, .05) for x in xs) and np.all(np.diff([f(x, .05) for x in xs]) >= 0)


def test_schedule_is_balanced_random_pairs_and_stops_when_done():
  clock = Clock()
  exp = make(clock, blocks=10, block_s=30)
  assert sorted(exp.order) == ['A'] * 5 + ['B'] * 5
  assert all(sorted(exp.order[i:i + 2]) == ['A', 'B'] for i in range(0, 10, 2))
  mixes = drive(exp, clock, 10 * 30 + 3)
  assert exp.state == 'done' and exp.index == 10
  assert len([r for r in exp._records if r['valid']]) == 10
  assert [r['mode'] for r in exp._records] == exp.order
  assert drive(exp, clock, 2)[-1] == 0.                              # back to stock once finished
  assert max(mixes) == 1.


def test_ramps_between_modes_and_never_jumps():
  clock = Clock()
  exp = make(clock, blocks=6, block_s=30)
  mixes = np.array(drive(exp, clock, 6 * 30))
  assert np.max(np.abs(np.diff(mixes))) <= .01 / se.RAMP_S + 1e-9


def test_gap_voids_the_block_and_restarts_it():
  clock = Clock()
  exp = make(clock, blocks=4, block_s=30)
  drive(exp, clock, 20)
  drive(exp, clock, 1.0, pressed=True)                               # the driver steers
  assert exp.acc == 0 and exp._records[-1]['valid'] is False and exp._records[-1]['reason'] == 'gap'
  assert exp.index == 0
  drive(exp, clock, 31)
  assert exp.index == 1 and exp._records[-1]['valid'] is True
  first = exp.index
  drive(exp, clock, 0.3, v=25.)                                      # a blip shorter than the gap limit does not void
  assert exp.index == first and exp.acc > 0


@pytest.mark.parametrize('kw', [dict(active=False), dict(pressed=True), dict(v=5.), dict(v=30.), dict(curv=.01), dict(a=2.)])
def test_only_steady_engaged_driving_counts_and_reverts_to_stock(kw):
  clock = Clock()
  exp = make(clock, blocks=4, block_s=30)
  exp.order = ['B', 'A', 'B', 'A']
  drive(exp, clock, 10)
  assert exp.mix > 0
  mixes = drive(exp, clock, 2., **kw)
  assert mixes[-1] == 0. and exp.qualifying is False


def test_writes_status_blocks_and_clears_the_param_when_done(tmp_path):
  clock = Clock()
  cleared = []
  exp = se.SteerExperiment(se.parse_config(cfg(blocks=2, block_s=30), wall_now=NOW), root=tmp_path, mono=clock,
                           wall=lambda: NOW, start_thread=False, on_done=lambda: cleared.append(1))
  drive(exp, clock, 31)
  exp.flush()
  status = json.loads((tmp_path / 'status.json').read_text())
  assert status['state'] == 'running' and status['block'] == 2 and status['of'] == 2
  drive(exp, clock, 31)
  exp.flush()
  exp.flush()
  lines = [json.loads(x) for x in (tmp_path / 'blocks.jsonl').read_text().splitlines()]
  assert [r['block'] for r in lines] == [0, 1] and all(r['valid'] for r in lines) and lines[0]['t1'] > lines[0]['t0']
  assert json.loads((tmp_path / 'status.json').read_text())['state'] == 'done' and cleared == [1]


def test_unwritable_status_directory_is_harmless(tmp_path):
  blocker = tmp_path / 'file'
  blocker.write_text('x')
  exp = se.SteerExperiment(se.parse_config(cfg(), wall_now=NOW), root=blocker / 'sub', mono=Clock(), wall=lambda: NOW, start_thread=False)
  with pytest.raises(OSError):
    exp.flush()                                                      # the writer thread catches this; the loop never sees it
  assert exp.step(True, 15.6, 0., False, 0.) >= 0.


# ---- integration with the real Volt controller -------------------------------------------------------------------

def controller(params_get=None, exp=None):
  CP = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  lac = LatControlPID(CP.as_reader(), CarInterface(CP), DT_CTRL)
  lac.exp = exp
  return lac, VehicleModel(CP)


def curv_of(t):
  return 0.0007 * np.sin(2 * np.pi * .4 * t)


def run(lac, VM, n=3000, curv=curv_of):
  CS = car.CarState.new_message(vEgo=15.6, aEgo=0.)
  params = log.LiveParametersData.new_message()
  out = []
  angle = 0.
  for k in range(n):
    CS.steeringAngleDeg = float(angle)
    u, _, pl = lac.update(True, CS, VM, params, False, float(curv(k * .01)), False, 0.2)
    angle += (10. * float(u) - angle) * .01 / .3
    out.append((float(u), float(pl.f)))
  return np.array(out)


def test_no_experiment_is_bit_for_bit_stock():
  a = run(*controller())
  b = run(*controller())
  assert np.array_equal(a, b)


def test_mode_a_is_bit_for_bit_stock_and_mode_b_adds_only_bounded_friction():
  clock = Clock()
  stock = run(*controller())
  exp = make(clock, blocks=2, block_s=30)
  exp.order = ['A', 'B']
  lac, VM = controller(exp=exp)
  monotonic_patch = exp.mono
  assert monotonic_patch is clock
  # drive the experiment clock in step with the controller
  CS = car.CarState.new_message(vEgo=15.6, aEgo=0.)
  params = log.LiveParametersData.new_message()
  angle, out = 0., []
  for k in range(3000):
    clock.t += .01
    CS.steeringAngleDeg = float(angle)
    u, _, pl = lac.update(True, CS, VM, params, False, curv_of(k * .01), False, 0.2)
    angle += (10. * float(u) - angle) * .01 / .3
    out.append((float(u), float(pl.f)))
  out = np.array(out)
  n = 2900   # still inside block A (30 s = 3000 steps): mix is 0 the whole time
  assert np.array_equal(out[:n], stock[:n])
  # B block: friction on, and never above the cap
  for k in range(3000, 6000):
    clock.t += .01
    CS.steeringAngleDeg = float(angle)
    u, _, pl = lac.update(True, CS, VM, params, False, curv_of(k * .01), False, 0.2)
    angle += (10. * float(u) - angle) * .01 / .3
    ff_only = lac.ff_factor * lac.get_steer_feedforward(np.degrees(VM.get_steer_from_curvature(-curv_of(k * .01), 15.6, 0.)), 15.6)
    assert abs(float(pl.f) - ff_only) <= se.FRICTION_MAX + 1e-9
  assert lac.exp.state in ('running', 'done')


def test_experiment_error_falls_back_to_stock():
  class Broken:
    cfg = {'kp': 1., 'ff': 1., 'friction': .05}

    def step(self, *a):
      raise RuntimeError('simulated')
  lac, VM = controller(exp=Broken())
  a = run(lac, VM)
  assert lac.exp is None
  assert np.array_equal(a, run(*controller()))


def test_arm_writes_a_clamped_config_marker_and_sets_old_blocks_aside(tmp_path):
  class FakeParams:
    def __init__(self):
      self.d = {}

    def put(self, k, v):
      self.d[k] = v

    def remove(self, k):
      self.d.pop(k, None)

  (tmp_path / 'blocks.jsonl').write_text('{"old": 1}\n')
  fp = FakeParams()
  parsed = se.arm(friction=5., blocks=10, params=fp, root=tmp_path)
  assert parsed['friction'] == se.FRICTION_MAX and se.PARAM in fp.d
  assert (tmp_path / 'armed').exists() and not (tmp_path / 'blocks.jsonl').exists()
  assert len(list(tmp_path.glob('blocks_*.jsonl'))) == 1
  assert se.parse_config(fp.d[se.PARAM]) is not None            # what arm wrote is accepted by the controller side
  se.disarm(fp)
  assert se.PARAM not in fp.d
