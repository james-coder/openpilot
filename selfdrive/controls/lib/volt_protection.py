"""Volt collision supervisor independent of MPC and personal comfort control.

No I/O, optimizer or unbounded history. Calibration is injected once at startup.
Monitor output cannot actuate without separately authorized backend authority.
"""

from dataclasses import dataclass, replace
from bisect import bisect_left
import math

from opendbc.car.gm.volt_protection import ProtectionAuthority
from openpilot.selfdrive.controls.lib.volt_collision import Envelope, assess


@dataclass(frozen=True)
class ProtectionCalibration:
  envelope: Envelope
  authority: ProtectionAuthority
  max_speed: float
  max_grade: float
  radar_to_bumper: float
  ego_half_width: float
  target_half_width: float
  range_margin: float
  lateral_margin: float
  velocity_margin: float

  def valid(self):
    values = (self.max_speed, self.max_grade, self.radar_to_bumper, self.ego_half_width, self.target_half_width,
              self.range_margin, self.lateral_margin, self.velocity_margin)
    return (all(math.isfinite(v) for v in values) and self.envelope.valid() and self.authority.valid()
            and self.envelope.ego_brake == self.authority.brake_decelerations[-1]
            and self.envelope.max_observation_age == self.authority.observation_age
            and 0 < self.max_speed <= 40 and 0 < self.max_grade <= .1 and -2 <= self.radar_to_bumper <= 2
            and .5 <= self.ego_half_width <= 1.5 and .2 <= self.target_half_width <= 2
            and 0 < self.range_margin <= 5 and 0 < self.lateral_margin <= 1 and 0 < self.velocity_margin <= 3)


@dataclass(frozen=True)
class Decision:
  state: str = 'unavailable'
  ceiling: float = 0.
  floor: int = 0
  track: int = -1
  minimum_gap: float = 0.
  assessed: bool = False
  reason: str = 'missing_calibration'
  observation_ns: int = 0

  @property
  def intervening(self):
    return self.state in ('protective', 'emergency', 'degraded', 'holding') and self.floor > 0


def plan_input(plan, valid, age, last_accel):
  """A stale plan cannot supply propulsion, including during soft-disable grace."""
  fresh = valid and math.isfinite(age) and 0 <= age <= .15 and math.isfinite(plan.aTarget)
  if fresh:
    return float(plan.aTarget), bool(plan.shouldStop), bool(plan.voltStopTrajectoryActive), True
  return min(0., last_accel) if math.isfinite(last_accel) else 0., True, False, False


def in_path(lead, model, cal):
  xs, ys = model.position.x, model.position.y
  if len(xs) != len(ys) or not 2 <= len(xs) <= 33:
    return None
  xs, ys = tuple(xs), tuple(ys)
  if not all(math.isfinite(v) for v in (*xs, *ys)):
    return None
  if any(a >= b for a, b in zip(xs[:-1], xs[1:], strict=True)):
    return None
  # radard converts model-frame lead coordinates to its radar origin with 1.52 m
  # and negates model y. Use the SAME frame transform for the predicted path.
  x = lead.dRel + 1.52
  if not xs[0] <= x <= xs[-1]:
    return None
  i = max(1, bisect_left(xs, x))
  y = ys[i-1] + (ys[i]-ys[i-1]) * (x-xs[i-1]) / (xs[i]-xs[i-1])
  return abs(lead.yRel + y) <= cal.ego_half_width + cal.target_half_width + cal.lateral_margin


class ProtectionMonitor:
  def __init__(self, calibration=None):
    if calibration is not None and not calibration.valid():
      raise ValueError('Invalid protection calibration')
    self.cal = calibration
    self.reset()

  def reset(self):
    self.decision = Decision(reason='monitoring', state='monitoring') if self.cal else Decision()
    self.last_observation = 0
    self.last_update = None
    self.clear_since = self.stopped_since = None
    self.tracks = {}
    self.demand_since = None

  def degraded(self, reason):
    self.clear_since = None
    d = self.decision
    self.decision = replace(d, state='degraded', reason=reason) if d.intervening else Decision(state='degraded', reason=reason)
    return self.decision

  def update(self, now, cs, radar, model, *, radar_age, model_age, ego_age, pitch, radar_delay=0., permitted=True):
    cal = self.cal
    if not permitted or cs.brakePressed or cs.gasPressed or cs.regenBraking or not cs.canValid:
      self.reset()
      return self.decision
    if cal is None:
      return self.decision
    if self.last_update is not None and now < self.last_update:
      self.reset()
      return self.degraded('clock_reset')
    self.last_update = now
    if (not all(math.isfinite(v) for v in (now, cs.vEgoRaw, cs.aEgo, pitch, radar_delay, ego_age))
        or not 0 <= ego_age <= .1 or not 0 <= cs.vEgoRaw <= cal.max_speed or abs(math.tan(pitch)) > cal.max_grade
        or str(cs.gearShifter) not in ('drive', 'low') or cs.espDisabled or not 0 <= radar_delay <= .3):
      return self.degraded('outside_calibrated_conditions')
    stationary = cs.standstill and abs(cs.vEgoRaw) < .03
    self.stopped_since = (self.stopped_since if self.stopped_since is not None else now) if stationary else None
    if self.decision.intervening and self.stopped_since is not None and now-self.stopped_since >= .2-1e-9:
      self.decision = replace(self.decision, state='holding', ceiling=0., floor=cal.authority.hold_brake, reason='confirmed_wheel_stop')
      return self.decision
    matched_model_age = now-radar.mdMonoTime/1e9
    if (radar.mdMonoTime == 0
        or not all(math.isfinite(v) and 0 <= v <= cal.envelope.max_observation_age for v in (radar_age, model_age, matched_model_age))
        or any(radar.radarErrors.to_dict().values())):
      return self.degraded('stale_or_faulted_perception')

    # Work only once per sensor observation; repeated 100 Hz reads cannot create
    # track confirmation or clear an intervention using a frozen radar frame.
    stamp = max(radar.leadOne.observationMonoTime, radar.leadTwo.observationMonoTime)
    if stamp > round(now*1e9):
      return self.degraded('future_observation')
    if stamp and stamp <= self.last_observation:
      if now-stamp/1e9+radar_delay > cal.envelope.max_observation_age:
        return self.degraded('stale_observation')
      return self.decision
    self.last_observation = stamp
    choices, unknown, pending, present = [], False, False, set()
    for lead in (radar.leadOne, radar.leadTwo):
      if not lead.status:
        continue
      numbers = (lead.dRel, lead.yRel, lead.vLead, lead.vRel, lead.visionProbability)
      if not all(math.isfinite(v) for v in numbers):
        unknown = True
        continue
      overlap = in_path(lead, model, cal)
      if overlap is False:
        continue
      age = now - lead.observationMonoTime / 1e9 + radar_delay
      qualified = (overlap is True and lead.radar and lead.measured and lead.visionMatched and lead.visionProbability >= .9
                   and lead.radarTrackId >= 0 and lead.observationMonoTime > 0 and 0 <= age <= cal.envelope.max_observation_age)
      if not qualified:
        unknown = True
        continue
      identity = lead.radarTrackId
      if identity in present:
        continue  # Both model lead slots can refer to the same radar object.
      present.add(identity)
      previous = self.tracks.get(identity)
      # Range must evolve plausibly across distinct observations; a reused ID
      # or a sudden jump cannot inherit confirmation from the old object.
      consistent = previous and 0 < now-previous[0] <= .3 and abs(lead.dRel-(previous[1]+previous[2]*(now-previous[0]))) <= cal.range_margin
      self.tracks[identity] = (now, lead.dRel, lead.vRel)
      if not consistent:
        pending = True
        continue
      env = cal.envelope
      gap = (lead.dRel - cal.radar_to_bumper - cal.range_margin - max(0., -lead.vRel)*age
             - .5*(env.lead_brake+env.acceleration_during_response)*age**2 - 2*cal.velocity_margin*age)
      ego = cs.vEgoRaw + cal.velocity_margin
      other = max(0., lead.vLead-env.lead_brake*age-cal.velocity_margin)
      result = assess(gap, ego, other, 0., env, in_path=True, confirmed=True)
      needed = result['required_brake']
      if needed is None:
        unknown = True
        continue
      if needed > env.comfort_brake or result['state'] == 'mitigation':
        emergency = result['state'] == 'mitigation' or result['maximum_brake_requested']
        choices.append(Decision('emergency' if emergency else 'protective', -min(4., needed),
                                400 if emergency else cal.authority.floor(needed), identity,
                                result['minimum_gap_at_limit'], True, 'braking_envelope', lead.observationMonoTime))
    self.tracks = {k: v for k, v in self.tracks.items() if k in present}
    if choices:
      choice = min(choices, key=lambda d: (d.ceiling, d.minimum_gap))
      # Loss of one target cannot erase a stronger previously established threat.
      if (unknown or pending or self.decision.track not in present) and self.decision.intervening and self.decision.ceiling < choice.ceiling:
        return self.degraded('target_uncertain')
      self.clear_since = None
      if self.demand_since is None or choice.floor > self.decision.floor:
        self.demand_since = now
      self.decision = choice
      if (self.demand_since is not None and now-self.demand_since > cal.envelope.response_seconds + .2
          and cs.aEgo > choice.ceiling + .5 and not stationary):
        self.decision = replace(choice, state='emergency', floor=400, ceiling=-min(4., cal.envelope.ego_brake), reason='response_shortfall')
      return self.decision
    if unknown or self.decision.intervening and (pending or self.decision.track not in present):
      return self.degraded('target_uncertain_or_lost')
    if pending:
      self.decision = Decision(state='monitoring', reason='confirming_target')
      return self.decision
    if self.decision.intervening:
      # Same positively identified target outside the protective envelope for
      # 0.5 s before easing. A stop requires driver resume/re-engagement instead.
      if self.decision.state == 'holding':
        return self.decision
      self.clear_since = self.clear_since if self.clear_since is not None else now
      if now-self.clear_since < .5:
        self.decision = replace(self.decision, state='protective', observation_ns=stamp,
                                reason='confirming_clearance')
        return self.decision
      index = max(0, bisect_left(cal.authority.brake_commands, self.decision.floor)-1)
      floor = cal.authority.brake_commands[index]
      self.decision = replace(self.decision, state='protective', floor=floor,
                              ceiling=-cal.authority.brake_decelerations[index], observation_ns=stamp,
                              reason='confirmed_clear_release')
      if floor:
        return self.decision
    self.decision = Decision(state='monitoring', reason='no_confirmed_threat')
    self.demand_since = None
    return self.decision


def write_request(target, decision, now_ns, profile_id, actuating):
  target.active = bool(actuating and decision.intervening)
  target.state = decision.state
  target.accelCeiling = decision.ceiling
  target.brakeFloor = decision.floor
  target.monoTime = now_ns
  target.profileId = profile_id
  target.trackId = decision.track
  target.minimumGap = decision.minimum_gap
  target.assessed = decision.assessed
  target.reason = decision.reason
  target.observationMonoTime = decision.observation_ns
