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
from opendbc.car.gm.carstate import CarState as GMCarState
from opendbc.car.gm.values import CAR, DBC
from opendbc.car.gm.volt_longitudinal import VoltFlags
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.controls.lib.volt_stopping import VoltStopping
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.tools.profiling.volt_pressure_model import PressureDynamics, allocator_profile

DT = 0.01


def volt_params(smooth=False, recorded=None):
  cp = CarInterface.get_non_essential_params(CAR.CHEVROLET_VOLT)
  for key, value in (recorded or {}).items():
    if key == 'longitudinalTuning':
      for field, values in value.items():
        setattr(cp.longitudinalTuning, field, values)
    else:
      setattr(cp, key, value)
  cp.flags &= ~int(VoltFlags.SMOOTH | VoltFlags.PERSONAL)
  if smooth:
    # Simulation only: startup configure() refuses unvalidated profiles.
    cp.flags |= int(VoltFlags.SMOOTH)
  return cp


class VoltPlant:
  def __init__(self, fit, speed=0.0, smooth=False, grade=0.0, regen_factor=1.0, extra_delay=0.0, stopping_profile=None, recorded_params=None):
    if stopping_profile is not None and not smooth:
      raise ValueError('A simulated stopping profile requires the candidate controller')
    self.CP = volt_params(smooth, recorded_params)
    self.estimator = GMCarState(self.CP)
    self.estimator.v_ego_kf.set_x([[speed], [0.]])
    self.long = LongControl(self.CP)
    if stopping_profile is not None:
      self.long.volt_stopping = VoltStopping(stopping_profile)
    self.controller = CarController(DBC[CAR.CHEVROLET_VOLT], self.CP)
    if smooth and 'pressure_model' in fit:
      self.controller.volt_profile = allocator_profile(fit['pressure_model'])
    self.state = car.CarState.new_message(vEgo=speed)
    self.cs = SimpleNamespace(
      out=self.state, loopback_lka_steering_cmd_ts_nanos=0, loopback_lka_steering_cmd_updated=False, pt_lka_steering_cmd_counter=0, volt_engine_running=False
    )
    self.cc = car.CarControl.new_message(enabled=True, longActive=True)
    self.cc.orientationNED = [0.0, float(np.arctan(grade)), 0.0]
    self.fit = fit
    self.dynamics = PressureDynamics(fit['pressure_model'], extra_delay=extra_delay) if 'pressure_model' in fit else None
    self.coef = fit['coefficients']
    self.grade, self.regen_factor = grade, regen_factor
    self.command_delay = deque([(0.0, 0.0)] * (1 + round((fit['delay'] + extra_delay) / DT)))
    self.gas = self.brake = self.regen = self.x = self.a = 0.0
    self.v = self.sensed_v = speed
    self.tick = 0

  def step(self, target, should_stop=False, active=True, brake_pressed=False, recorded_command=None):
    self.state.vEgo = self.sensed_v
    self.state.aEgo = self.a
    self.state.standstill = self.v <= 0.0864
    self.state.brakePressed = brake_pressed
    # Match the normal controls contract: a pedal override removes actuation.
    self.cc.longActive = active and not brake_pressed
    command = self.long.update(self.cc.longActive, self.state, target, should_stop, (-4.0, 2.0))
    self.cc.actuators.accel = float(command)
    self.cc.actuators.longControlState = self.long.long_control_state
    applied, _discarded_can = self.controller.update(self.cc.as_reader(), self.cs, int((1 + self.tick * DT) * 1e9))
    if recorded_command is not None:
      applied.gas, applied.brake = recorded_command['applied_gas'], recorded_command['applied_brake']
      command = recorded_command.get('cmd', 0.)
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
    if self.dynamics is not None:
      physical_accel = self.dynamics.step(applied.gas, applied.brake, self.v, np.arctan(self.grade), self.regen_factor)
    previous = self.v
    physical_accel = float(physical_accel)
    self.v = max(0.0, self.v + physical_accel * DT)
    # Contact at rest prevents integrating negative forward speed; rollback is
    # assessed as insufficient holding force, not hidden by this clamp.
    self.x += (previous + self.v) * 0.5 * DT
    # Match the GM wheel-speed quantization and production KF, rather than
    # fitting comfort metrics through an arbitrary acceleration low-pass filter.
    wheel_quantum = 0.0311 / 3.6 * self.CP.wheelSpeedFactor
    sensed = round(self.v / wheel_quantum) * wheel_quantum
    self.sensed_v, self.a = self.estimator.update_speed_kf(sensed)
    self.tick += 1
    return {
      't': self.tick * DT,
      'v': self.sensed_v,
      'physical_v': self.v,
      'a': self.a,
      'physical_accel': physical_accel,
      'x': self.x,
      'target': target,
      'cmd': float(command),
      'brake': float(applied.brake),
      'gas': float(applied.gas),
      'i': float(self.long.pid.i),
      'state': str(self.long.long_control_state),
      'pressure': self.dynamics.pressure * 30000 if self.dynamics is not None else None,
      'active': self.cc.longActive,
      'should_stop': bool(should_stop),
    }


def replay_targets(event, fit, smooth=False, stopping_profile=None):
  rows = event['samples']
  times = np.array([r['t'] for r in rows])
  controls = [r for r in rows if r.get('active') and 'target' in r]
  if not controls:
    raise ValueError('No valid autonomous planner targets')
  control_records = sorted({r.get('sample_times', {}).get('longitudinalPlan', r['t']): r for r in controls}.items())
  controls = [r[1] for r in control_records]
  ts = np.array([r[0] for r in control_records])
  targets = np.array([r['target'] for r in controls])
  first = next(r for r in rows if r['t'] >= ts[0])
  plant = VoltPlant(fit, speed=first['vraw'], smooth=smooth, stopping_profile=stopping_profile, recorded_params=event.get('car_params'))
  trace = []
  for t in np.arange(first['t'], times[-1], DT):
    idx = min(len(controls) - 1, max(0, int(np.searchsorted(ts, t, side='right') - 1)))
    if t - ts[idx] > .3:
      raise ValueError('Planner target gap exceeds 0.3 s')
    r = plant.step(float(targets[idx]), controls[idx].get('should_stop', False))
    r['t'] = float(t)
    r['recorded_a'] = float(np.interp(t, times, [x['a'] for x in rows]))
    trace.append(r)
  return trace


def closed_loop_stop(
  fit, speed=10.0, distance=45.0, smooth=False, grade=0.0, regen_factor=1.0, extra_delay=0.0, personality=log.LongitudinalPersonality.standard,
  stopping_profile=None, approach_profile=None, scenario='stationary',
):
  plant = VoltPlant(fit, speed, smooth, grade, regen_factor, extra_delay, stopping_profile)
  planner = LongitudinalPlanner(plant.CP, init_v=speed)
  planner.mpc.set_personal_curve(approach_profile)
  sm = {name: getattr(messaging.new_message(name), name) for name in ('carState', 'controlsState', 'selfdriveState', 'liveParameters', 'carControl', 'modelV2')}
  sm['selfdriveState'].enabled = True
  sm['selfdriveState'].personality = personality
  sm['carControl'].orientationNED = [0.0, float(np.arctan(grade)), 0.0]
  radar = log.RadarState.new_message()
  radar.leadOne.status = True
  radar.leadOne.modelProb = 1.0
  radar.leadOne.aLeadTau = 1.5
  radar.leadOne.radar = True
  radar.leadOne.radarTrackId = 1
  sm['radarState'] = radar
  sm['radar_age'] = 0.
  target = 0.0
  should_stop = False
  trace = []
  lead_x = distance
  lead_v = speed if scenario == 'moving_stop' else 0.
  if scenario == 'engine_on':
    plant.cs.volt_engine_running = True
    plant.regen_factor *= .5
  for k in range(3000):
    elapsed = k * DT
    lead_a = -1.5 if scenario == 'moving_stop' and elapsed >= 2 and lead_v > 0 else 0.
    if scenario == 'cut_in' and k == 200:
      lead_x, lead_v = plant.x + 15., 2.
      radar.leadOne.radarTrackId = 2
    if scenario in ('pull_away', 'stop_resume'):
      lead_a = .8 if 8 <= elapsed < 12 else -1. if scenario == 'stop_resume' and elapsed >= 15 and lead_v > 0 else 0.
    lead_v = max(0., lead_v + lead_a * DT)
    lead_x += lead_v * DT
    if k % 5 == 0:
      radar.leadOne.status = not (scenario == 'lead_loss' and 3 <= elapsed < 4)
      radar.leadOne.dRel = lead_x - plant.x
      radar.leadOne.vLead = lead_v
      radar.leadOne.vRel = lead_v - plant.sensed_v
      radar.leadOne.aLeadK = lead_a
      sm['carState'].vEgo = plant.sensed_v
      sm['carState'].aEgo = plant.a
      sm['carState'].standstill = plant.v <= 0.0864
      sm['carState'].vCruise = speed * 3.6
      sm['controlsState'].longControlState = plant.long.long_control_state
      planner.update(sm)
      target, should_stop = planner.output_a_target, planner.output_should_stop
    override = scenario == 'pedal_override' and 3 <= elapsed < 3.5
    urgent = scenario == 'full_braking' and 2 <= elapsed < 3
    row = plant.step(-4. if urgent else float(target), bool(should_stop), brake_pressed=override)
    row['gap'] = lead_x - plant.x
    row['personal_blend'] = planner.mpc.personal_blend
    row['solver_status'] = planner.mpc.solution_status
    trace.append(row)
    if scenario in ('stationary', 'engine_on', 'moving_stop') and k > 300 and plant.v < 0.01 and all(r['v'] < 0.01 for r in trace[-100:]):
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
    'stop_time': end,
    'settled_gap': float(np.median([r['gap'] for r in trace if end + .5 <= r['t'] <= end + 2]))
    if 'gap' in trace[0] and any(end + .5 <= r['t'] <= end + 2 for r in trace) else None,
    'braking_onset': next((r['t'] for r in trace if r['a'] < -.3), None),
    'solver_failures': sum(r.get('solver_status', 0) != 0 for r in trace),
  }
