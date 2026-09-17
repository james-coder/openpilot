"""Socket-free protection integration bench. All physical values are hypothetical.

Exercises production controls arbitration and GM CAN packing. This fixture is
deliberately separate from the installed calibration and cannot qualify a car.
"""

import argparse
from collections import deque
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from cereal import car, log
from opendbc.car.gm.carcontroller import CarController
from opendbc.car.gm.values import CAR, DBC
from opendbc.car.gm.volt_protection import ProtectionAuthority, ProtectionGate
from openpilot.selfdrive.car.volt_protection import ACTUATE
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.controls.lib.volt_collision import Envelope
from openpilot.selfdrive.controls.lib.volt_protection import ProtectionCalibration, ProtectionMonitor
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import volt_params


def hypothetical_calibration():
  return ProtectionCalibration(Envelope(.5, 4., 8., 1., .5, .15, 2.5),
                               ProtectionAuthority('f'*64, (0, 100, 200, 300, 400), (0., 1., 2., 3., 4.), 200, .15),
                               35., .05, 0., .9, .9, .5, .2, .1)


class Inputs(dict):
  def __init__(self):
    super().__init__()
    self.valid, self.alive, self.logMonoTime = {}, {}, {}

  def fresh(self, now_ns):
    for name in ('radarState', 'modelV2', 'carState', 'livePose', 'longitudinalPlan'):
      self.valid[name] = self.alive[name] = True
      self.logMonoTime[name] = now_ns


class ProtectionBench:
  def __init__(self, *, actuate=True, smooth=False):
    self.cal = hypothetical_calibration()
    self.cp = volt_params(smooth)
    self.cp.radarDelay = 0.
    if actuate:
      self.cp.flags |= ACTUATE
    self.controls = Controls.__new__(Controls)  # No process startup, params, messaging or sockets.
    self.controls.CP = self.cp
    self.controls.volt_supported = True
    self.controls.LoC = LongControl(self.cp)
    self.controls.protection = ProtectionMonitor(self.cal)
    self.controls.calibrated_pose = SimpleNamespace(orientation=SimpleNamespace(xyz=[0., 0., 0.]))
    self.sm = self.controls.sm = Inputs()
    self.radar = self.sm['radarState'] = log.RadarState.new_message()
    self.model = log.ModelDataV2.new_message()
    self.model.position.x = [float(i*5) for i in range(33)]
    self.model.position.y = [0.] * 33
    self.state = car.CarState.new_message(canValid=True, gearShifter='drive', vEgo=10., vEgoRaw=10.)
    self.plan = log.LongitudinalPlan.new_message(aTarget=1., shouldStop=False)
    self.controller = CarController(DBC[CAR.CHEVROLET_VOLT], self.cp)
    self.controller.protection_gate = ProtectionGate(self.cal.authority if actuate else None)
    self.cs = SimpleNamespace(out=self.state, loopback_lka_steering_cmd_ts_nanos=0,
                              loopback_lka_steering_cmd_updated=False, pt_lka_steering_cmd_counter=0, volt_engine_running=False)
    self.ns = 1_000_000_000
    self.sm.fresh(self.ns)

  def lead(self, distance=20., speed=0., lateral=0., identity=1, second=False, **fields):
    self.radar.mdMonoTime = self.ns
    target = self.radar.leadTwo if second else self.radar.leadOne
    values = dict(status=True, dRel=distance, vLead=speed, vRel=speed-self.state.vEgoRaw, yRel=lateral,
                  radar=True, measured=True, visionMatched=True, visionProbability=.99,
                  radarTrackId=identity, observationMonoTime=self.ns)
    values.update(fields)
    for name, value in values.items():
      setattr(target, name, value)

  def step(self, *, fresh=True, active=True):
    if fresh:
      self.sm.fresh(self.ns)
    cc = car.CarControl.new_message()
    cc.enabled, cc.longActive = active, active
    self.controls.update_longitudinal(cc, self.state, self.plan, self.model, (-4., 2.), self.ns)
    output, can = self.controller.update(cc.as_reader(), self.cs, self.ns)
    self.ns += 10_000_000
    return cc, output, can


def scenario(name, *, speed=10., gap=28., lead_speed=0., lead_brake=0., lateral=0., delay=.3, grip=1., grade=0.,
             lost_at=None, cut_in_at=None, override_at=None):
  bench = ProtectionBench()
  bench.controls.calibrated_pose.orientation.xyz[1] = grade
  queue = deque([0.] * max(1, round(delay/.01)))
  v, other, distance, a = speed, lead_speed, gap, 0.
  rows, minimum, first, stopped_at = [], gap, None, None
  for tick in range(1800):
    t = tick*.01
    bench.state.vEgo = bench.state.vEgoRaw = v
    bench.state.aEgo, bench.state.standstill = a, v < .03
    bench.state.brakePressed = override_at is not None and t >= override_at
    if tick % 5 == 0:
      if lost_at is not None and t >= lost_at:
        bench.radar.leadOne.status = False
      else:
        bench.lead(distance, other, lateral=3.5 if cut_in_at is not None and t < cut_in_at else lateral)
    cc, out, can = bench.step()
    # Independent declared plant: no regen contribution; delayed friction only.
    # 4 m/s² at brake400 is a TEST ASSUMPTION, never a vehicle measurement.
    queue.append(out.brake*.01*grip)
    a = -queue.popleft() + 9.81*grade
    if bench.state.brakePressed:
      a = -5.  # Declared driver braking after takeover, outside openpilot control.
    next_v, next_other = max(0., v+a*.01), max(0., other-lead_brake*.01)
    distance += .005*(other+next_other-v-next_v)
    v, other = next_v, next_other
    stopped_at = (stopped_at if stopped_at is not None else t) if v == 0. else None
    minimum = min(minimum, distance)
    if cc.longitudinalProtection.active and first is None:
      first = t
    if can and tick % 4 == 0:
      rows.append({'t': round(t, 2), 'gap': round(distance, 3), 'speed': round(v, 3),
                   'state': str(cc.longitudinalProtection.state), 'reason': cc.longitudinalProtection.reason,
                   'requested_accel': float(cc.actuators.accel), 'requested_floor': cc.longitudinalProtection.brakeFloor,
                   'applied_brake': out.brake, 'backend_accepted': bench.controller.protection_gate.accepted,
                   'backend_reason': bench.controller.protection_gate.reason})
    if stopped_at is not None and t-stopped_at >= .5 or distance < -2.:
      break
  bounded = all(-4.00001 <= row['requested_accel'] <= 2. and 0 <= row['applied_brake'] <= 400 for row in rows)
  arbitration = all(row['applied_brake'] >= row['requested_floor'] for row in rows if row['backend_accepted'])
  return {'name': name, 'minimum_gap': minimum, 'first_intervention': first, 'bounded': bounded, 'arbitration_pass': arbitration,
          'collision_in_assumed_plant': minimum <= 0. and abs(lateral) < 2., 'trace': rows}


def run_scenarios():
  cases = [scenario('stationary target'), scenario('moving target hard braking', gap=32., lead_speed=8., lead_brake=8.),
           scenario('cut in', gap=28., cut_in_at=.2), scenario('adjacent vehicle', gap=8., lateral=3.5),
           scenario('response delay bound', delay=.5), scenario('target loss during intervention', lost_at=.6),
           scenario('driver brake override', override_at=.8), scenario('insufficient initial distance', gap=8.),
           scenario('outside assumed friction bound', grip=.45), scenario('downhill', grade=.03)]
  avoidance = {'stationary target', 'moving target hard braking', 'cut in', 'response delay bound',
               'target loss during intervention', 'downhill', 'driver brake override'}
  for case in cases:
    if case['name'] in avoidance:
      behavior = case['minimum_gap'] >= hypothetical_calibration().envelope.clearance
    elif case['name'] == 'adjacent vehicle':
      behavior = case['first_intervention'] is None
    else:
      behavior = any(r['applied_brake'] == 400 for r in case['trace'])
    case['pass'] = case['bounded'] and case['arbitration_pass'] and behavior
  return {'version': 1, 'runtime_connected': True, 'vehicle_calibrated': False, 'road_ready': False,
          'scope': 'Synthetic production controls-to-CAN bench; no vehicle or socket access. No crash-avoidance guarantee.',
          'calibration': asdict(hypothetical_calibration()), 'cases': cases}


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('output', type=Path)
  args = parser.parse_args()
  args.output.write_text(json.dumps(run_scenarios(), indent=2, allow_nan=False)+'\n')
