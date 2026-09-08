"""Offline Volt command/actuator simulation. Never opens CAN sockets.

Runs LongControl at 100 Hz and the actual GM CarController (25 Hz longitudinal
CAN generation), then a separately fitted vehicle response. Generated CAN is
returned by the controller and discarded; no vehicle connection is possible.
"""

from collections import deque
from types import SimpleNamespace
import numpy as np
from cereal import car, log
import cereal.messaging as messaging
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.interface import CarInterface
from opendbc.car.gm.values import CAR, DBC
from opendbc.car.gm.volt_longitudinal import VoltFlags
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner

DT = 0.01


def volt_params(smooth=False):
  cp = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  if smooth:
    # Simulation only: startup configure() refuses unvalidated profiles.
    cp.flags |= int(VoltFlags.SMOOTH)
  return cp


class VoltPlant:
  def __init__(self, fit, speed=0.0, smooth=False, grade=0.0, regen_factor=1.0, extra_delay=0.0, stopping_profile=None):
    if stopping_profile is not None and not smooth:
      raise ValueError('A simulated stopping profile requires the candidate controller')
    self.CP = volt_params(smooth)
    self.long = LongControl(self.CP)
    if stopping_profile is not None:
      self.long.volt_stopping = VoltStopping(stopping_profile)
    self.controller = CarController(DBC[CAR.CHEVROLET_VOLT], self.CP)
    self.state = car.CarState.new_message(vEgo=speed)
    self.cs = SimpleNamespace(
      out=self.state, loopback_lka_steering_cmd_ts_nanos=0, loopback_lka_steering_cmd_updated=False, pt_lka_steering_cmd_counter=0, volt_engine_running=False
    )
    self.cc = car.CarControl.new_message(enabled=True, longActive=True)
    self.cc.orientationNED = [0.0, float(np.arctan(grade)), 0.0]
    self.fit = fit
    self.coef = fit['coefficients']
    self.grade, self.regen_factor = grade, regen_factor
    self.command_delay = deque([(0.0, 0.0)] * (1 + round((fit['delay'] + extra_delay) / DT)))
    self.gas = self.brake = self.regen = self.x = self.a = 0.0
    self.v = speed
    self.tick = 0

  def step(self, target, should_stop=False, active=True, brake_pressed=False):
    self.state.vEgo = self.v
    self.state.aEgo = self.a
    self.state.standstill = self.v <= 0.0864
    self.state.brakePressed = brake_pressed
    # Match the normal controls contract: a pedal override removes actuation.
    self.cc.longActive = active and not brake_pressed
    command = self.long.update(self.cc.longActive, self.state, target, should_stop, (-4.0, 2.0))
    self.cc.actuators.accel = float(command)
    self.cc.actuators.longControlState = self.long.long_control_state
    applied, _discarded_can = self.controller.update(self.cc.as_reader(), self.cs, int((1 + self.tick * DT) * 1e9))
    self.command_delay.append((applied.gas, applied.brake))
    gas, brake = self.command_delay.popleft()
    alpha = DT / (self.fit['tau'] + DT)
    self.gas += alpha * (max(0.0, gas) / 1018.0 - self.gas)
    self.regen += alpha * (max(0.0, -gas) / 650.0 - self.regen)
    self.brake += alpha * (brake / 400.0 - self.brake)
    regen_capacity = float(np.interp(self.v, self.fit['speed'], self.coef[2:-2]))
    physical_accel = (
      self.coef[0] * self.gas
      - self.coef[1] * self.brake
      - regen_capacity * self.regen * self.regen_factor
      + self.coef[-2] * max(0.0, 1 - self.v / 2.0)
      + self.coef[-1]
      - self.grade * 9.81
    )
    previous = self.v
    self.v = max(0.0, self.v + physical_accel * DT)
    # Contact at rest prevents integrating negative forward speed; rollback is
    # assessed as insufficient holding force, not hidden by this clamp.
    actual = (self.v - previous) / DT
    self.x += (previous + self.v) * 0.5 * DT
    self.a += DT / (0.1 + DT) * (actual - self.a)
    self.tick += 1
    return {
      't': self.tick * DT,
      'v': self.v,
      'a': self.a,
      'physical_accel': physical_accel,
      'x': self.x,
      'target': target,
      'cmd': float(command),
      'brake': float(applied.brake),
      'gas': float(applied.gas),
      'i': float(self.long.pid.i),
      'state': str(self.long.long_control_state),
    }


def replay_targets(event, fit, smooth=False, stopping_profile=None):
  rows = event['samples']
  times = np.array([r['t'] for r in rows])
  controls = [r for r in rows if r.get('active') and 'target' in r]
  if not controls:
    raise ValueError('No valid autonomous planner targets')
  ts = np.array([r['t'] for r in controls])
  targets = np.array([r['target'] for r in controls])
  plant = VoltPlant(fit, speed=rows[0]['v'], smooth=smooth, stopping_profile=stopping_profile)
  trace = []
  for t in np.arange(times[0], times[-1], DT):
    idx = min(len(controls) - 1, max(0, int(np.searchsorted(ts, t, side='right') - 1)))
    r = plant.step(float(np.interp(t, ts, targets)), controls[idx].get('should_stop', False))
    r['t'] = float(t)
    r['recorded_a'] = float(np.interp(t, times, [x['a'] for x in rows]))
    trace.append(r)
  return trace


def closed_loop_stop(
  fit, speed=10.0, distance=45.0, smooth=False, grade=0.0, regen_factor=1.0, extra_delay=0.0, personality=log.LongitudinalPersonality.standard,
  stopping_profile=None,
):
  plant = VoltPlant(fit, speed, smooth, grade, regen_factor, extra_delay, stopping_profile)
  planner = LongitudinalPlanner(plant.CP, init_v=speed)
  sm = {name: getattr(messaging.new_message(name), name) for name in ('carState', 'controlsState', 'selfdriveState', 'liveParameters', 'carControl', 'modelV2')}
  sm['selfdriveState'].enabled = True
  sm['selfdriveState'].personality = personality
  sm['carControl'].orientationNED = [0.0, float(np.arctan(grade)), 0.0]
  radar = log.RadarState.new_message()
  radar.leadOne.status = True
  radar.leadOne.modelProb = 1.0
  radar.leadOne.aLeadTau = 1.5
  sm['radarState'] = radar
  target = 0.0
  should_stop = False
  trace = []
  for k in range(3000):
    if k % 5 == 0:
      radar.leadOne.dRel = distance - plant.x
      radar.leadOne.vLead = 0.0
      radar.leadOne.aLeadK = 0.0
      sm['carState'].vEgo = plant.v
      sm['carState'].aEgo = plant.a
      sm['carState'].standstill = plant.v <= 0.0864
      sm['carState'].vCruise = speed * 3.6
      sm['controlsState'].longControlState = plant.long.long_control_state
      planner.update(sm)
      target, should_stop = planner.output_a_target, planner.output_should_stop
    row = plant.step(float(target), bool(should_stop))
    row['gap'] = distance - plant.x
    trace.append(row)
    if k > 300 and plant.v < 0.01 and all(r['v'] < 0.01 for r in trace[-100:]):
      break
  return trace


def metrics(trace):
  stopped = next((i for i in range(1, len(trace) - 100) if trace[i]['v'] < 0.3 and max(r['v'] for r in trace[i : i + 100]) < 0.5), len(trace) - 1)
  end = trace[stopped]['t']
  low_start = stopped
  while low_start > 0 and trace[low_start - 1]['v'] < 2 and trace[low_start]['t'] - trace[low_start - 1]['t'] < 0.1:
    low_start -= 1
  low = trace[low_start:stopped + 1]
  # Match the native review: centered 0.2 s difference, -3 to +0.5 s.
  times = np.array([r['t'] for r in trace])
  a = np.array([r['a'] for r in trace])
  jerk = (np.interp(times + 0.1, times, a) - np.interp(times - 0.1, times, a)) / 0.2
  jerk = jerk[(times >= end - 3) & (times <= end + 0.5)]
  rebound = max((r['v'] - min(p['v'] for p in low[: i + 1]) for i, r in enumerate(low)), default=0.0)
  return {
    'min_accel': min(r['a'] for r in trace),
    'final_jerk_p95': float(np.percentile(abs(jerk), 95)) if len(jerk) else None,
    'low_speed_seconds': end - low[0]['t'],
    'speed_rebound': rebound,
    'stopped': trace[-1]['v'] < 0.05,
    'minimum_gap': min((r.get('gap', float('inf')) for r in trace), default=None) if 'gap' in trace[0] else None,
  }
