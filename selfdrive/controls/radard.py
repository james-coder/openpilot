#!/usr/bin/env python3
import math
import numpy as np
from collections import deque
from typing import Any

import capnp
from cereal import messaging, log, car
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL, Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.common.simple_kalman import KF1D


# Default lead acceleration decay set to 50% at 1s
_LEAD_ACCEL_TAU = 1.5

# radar tracks
SPEED, ACCEL = 0, 1     # Kalman filter states enum

# stationary qualification parameters
V_EGO_STATIONARY = 4.   # no stationary object flag below this speed

RADAR_TO_CENTER = 2.7   # (deprecated) RADAR is ~ 2.7m ahead from center of car
RADAR_TO_CAMERA = 1.52  # RADAR is ~ 1.5m ahead from center of mesh frame

# Cut-in confidence: was this track observed drifting in from an adjacent lane before it became
# the selected lead, as opposed to e.g. the previous lead braking or a lead-selection swap
# between two already-in-path tracks? See cutin_confidence() below and
# selfdrive/controls/lib/longitudinal_mpc_lib/cutin_relaxation.py for how it's consumed.
CUTIN_VY_FILTER_TAU = 0.5        # s, low-pass on the raw yRel finite difference -- not a raw 1-frame derivative
CUTIN_MIN_TRACK_AGE_CYCLES = 20  # ~1.0s at DT_MDL=0.05s: must have history before it can count
CUTIN_MIN_LATERAL_OFFSET = 1.2   # m, peak |yRel| must have exceeded this to have been "adjacent"
CUTIN_OFFSET_SATURATE = 2.5      # m, peak |yRel| at/above this saturates the offset evidence
CUTIN_MIN_INWARD_VY = 0.15       # m/s, minimum filtered inward lateral speed to count as evidence
CUTIN_SATURATE_INWARD_VY = 0.7   # m/s, filtered inward lateral speed at/above this saturates
CUTIN_MAX_YAW_RATE = 0.05        # rad/s, ego yaw rate above this suppresses confidence (curved-road gate)
CUTIN_LOG_THRESHOLD = 0.5        # confidence above which a lead-selection transition is an accepted cut-in
CUTIN_LATCH_DURATION = 1.0       # s, see RadarD._cutin_latch's docstring
CUTIN_ADJACENCY_DECAY_TAU = 8.0  # s, decay time constant for "how recently was this track substantially
                                  # adjacent" -- recent evidence, not a lifetime-sticky peak (a car that
                                  # ran parallel long ago and has been in-path ever since shouldn't still
                                  # "count" toward a much-later, unrelated lead-selection transition).
                                  # Comfortably longer than a realistic several-second lane change so an
                                  # in-progress genuine cut-in isn't itself discounted away by the decay.


class KalmanParams:
  def __init__(self, dt: float):
    # Lead Kalman Filter params, calculating K from A, C, Q, R requires the control library.
    # hardcoding a lookup table to compute K for values of radar_ts between 0.01s and 0.2s
    assert dt > .01 and dt < .2, "Radar time step must be between .01s and 0.2s"
    self.A = [[1.0, dt], [0.0, 1.0]]
    self.C = [1.0, 0.0]
    #Q = np.matrix([[10., 0.0], [0.0, 100.]])
    #R = 1e3
    #K = np.matrix([[ 0.05705578], [ 0.03073241]])
    dts = [i * 0.01 for i in range(1, 21)]
    K0 = [0.12287673, 0.14556536, 0.16522756, 0.18281627, 0.1988689,  0.21372394,
          0.22761098, 0.24069424, 0.253096,   0.26491023, 0.27621103, 0.28705801,
          0.29750003, 0.30757767, 0.31732515, 0.32677158, 0.33594201, 0.34485814,
          0.35353899, 0.36200124]
    K1 = [0.29666309, 0.29330885, 0.29042818, 0.28787125, 0.28555364, 0.28342219,
          0.28144091, 0.27958406, 0.27783249, 0.27617149, 0.27458948, 0.27307714,
          0.27162685, 0.27023228, 0.26888809, 0.26758976, 0.26633338, 0.26511557,
          0.26393339, 0.26278425]
    self.K = [[np.interp(dt, dts, K0)], [np.interp(dt, dts, K1)]]


class Track:
  def __init__(self, identifier: int, v_lead: float, kalman_params: KalmanParams):
    self.identifier = identifier
    self.cnt = 0
    self.aLeadTau = FirstOrderFilter(_LEAD_ACCEL_TAU, 0.45, DT_MDL)
    self.K_A = kalman_params.A
    self.K_C = kalman_params.C
    self.K_K = kalman_params.K
    self.kf = KF1D([[v_lead], [0.0]], self.K_A, self.K_C, self.K_K)
    self.yRel = 0.0
    # instant rise / slow decay, same asymmetric idiom as RadarD.lead_prob_filters below --
    # "how adjacent has this track recently been", not a lifetime-sticky peak (see
    # CUTIN_ADJACENCY_DECAY_TAU)
    self.recent_abs_yRel = FirstOrderFilter(0.0, CUTIN_ADJACENCY_DECAY_TAU, DT_MDL)
    self.vy_filt = FirstOrderFilter(0.0, CUTIN_VY_FILTER_TAU, DT_MDL)

  def update(self, d_rel: float, y_rel: float, v_rel: float, v_lead: float, measured: float):
    # filtered estimate of lateral velocity, from the previous cycle's yRel -- not a raw
    # 1-frame derivative, per cutin_confidence()'s use of it below
    if self.cnt > 0:
      self.vy_filt.update((y_rel - self.yRel) / DT_MDL)

    # relative values, copy
    self.dRel = d_rel   # LONG_DIST
    self.yRel = y_rel   # -LAT_DIST
    self.vRel = v_rel   # REL_SPEED
    self.vLead = v_lead
    self.measured = measured   # measured or estimate
    if abs(self.yRel) > self.recent_abs_yRel.x:
      self.recent_abs_yRel.x = abs(self.yRel)
    else:
      self.recent_abs_yRel.update(abs(self.yRel))

    # computed velocity and accelerations
    if self.cnt > 0:
      self.kf.update(self.vLead)

    self.vLeadK = float(self.kf.x[SPEED][0])
    self.aLeadK = float(self.kf.x[ACCEL][0])

    # Learn if constant acceleration
    if abs(self.aLeadK) < 0.5:
      self.aLeadTau.x = _LEAD_ACCEL_TAU
    else:
      self.aLeadTau.update(0.0)

    self.cnt += 1

  def cutin_evidence(self, straight_road: bool, not_ego_lane_change: bool) -> dict:
    """Full component breakdown behind cutin_confidence(), for diagnostic logging -- every
    leadOne transition gets one of these logged (see RadarD.update()), accepted or not, so
    "that was obviously a cut-in, why did it score low?" is answerable from device logs
    without needing full replay/visualization tooling."""
    age_ok = self.cnt >= CUTIN_MIN_TRACK_AGE_CYCLES
    offset_score = np.clip((self.recent_abs_yRel.x - CUTIN_MIN_LATERAL_OFFSET) /
                            (CUTIN_OFFSET_SATURATE - CUTIN_MIN_LATERAL_OFFSET), 0., 1.)
    inward_vy = -math.copysign(1.0, self.yRel) * self.vy_filt.x if self.yRel else 0.0
    inward_score = np.clip((inward_vy - CUTIN_MIN_INWARD_VY) /
                            (CUTIN_SATURATE_INWARD_VY - CUTIN_MIN_INWARD_VY), 0., 1.)
    confidence = float(offset_score * inward_score) if (straight_road and not_ego_lane_change and age_ok) else 0.0
    return {
      'track_age_cycles': self.cnt, 'age_ok': age_ok, 'recent_abs_yRel': round(float(self.recent_abs_yRel.x), 2),
      'inward_vy': round(float(inward_vy), 2), 'straight_road': straight_road, 'not_ego_lane_change': not_ego_lane_change,
      'dRel': round(float(self.dRel), 1), 'vRel': round(float(self.vRel), 1), 'confidence': round(confidence, 3),
    }

  def cutin_confidence(self, straight_road: bool, not_ego_lane_change: bool) -> float:
    """0-1 confidence that this track was drifting in from an adjacent lane, from its own
    pre-selection history alone -- independent of whether it's actually selected as a lead
    this cycle. Gated to 0 outright on a curved road or while ego itself is lane-changing
    (scenario 7/8 in the cut-in relaxation plan); otherwise a product of how far adjacent it
    was and how fast it's currently moving toward the path, each saturating over a range
    rather than a single hard threshold."""
    return self.cutin_evidence(straight_road, not_ego_lane_change)['confidence']

  def get_RadarState(self, model_prob: float = 0.0):
    return {
      "dRel": float(self.dRel),
      "yRel": float(self.yRel),
      "vRel": float(self.vRel),
      "vLead": float(self.vLead),
      "vLeadK": float(self.vLeadK),
      "aLeadK": float(self.aLeadK),
      "aLeadTau": float(self.aLeadTau.x),
      "status": True,
      "fcw": self.is_potential_fcw(model_prob),
      "modelProb": model_prob,
      "radar": True,
      "radarTrackId": self.identifier,
      "measured": bool(self.measured),
    }

  def potential_low_speed_lead(self, v_ego: float):
    # stop for stuff in front of you and low speed, even without model confirmation
    # Radar points closer than 0.75, are almost always glitches on toyota radars
    return abs(self.yRel) < 1.0 and (v_ego < V_EGO_STATIONARY) and (0.75 < self.dRel < 25)

  def is_potential_fcw(self, model_prob: float):
    return model_prob > .9

  def __str__(self):
    ret = f"x: {self.dRel:4.1f}  y: {self.yRel:4.1f}  v: {self.vRel:4.1f}  a: {self.aLeadK:4.1f}"
    return ret


def laplacian_pdf(x: float, mu: float, b: float):
  b = max(b, 1e-4)
  return math.exp(-abs(x-mu)/b)


def match_vision_to_track(v_ego: float, lead: capnp._DynamicStructReader, tracks: dict[int, Track]):
  offset_vision_dist = lead.x[0] - RADAR_TO_CAMERA

  def prob(c):
    prob_d = laplacian_pdf(c.dRel, offset_vision_dist, lead.xStd[0])
    prob_y = laplacian_pdf(c.yRel, -lead.y[0], lead.yStd[0])
    prob_v = laplacian_pdf(c.vRel + v_ego, lead.v[0], lead.vStd[0])

    # This isn't exactly right, but it's a good heuristic
    return prob_d * prob_y * prob_v

  track = max(tracks.values(), key=prob)

  # if no 'sane' match is found return -1
  # stationary radar points can be false positives
  dist_sane = abs(track.dRel - offset_vision_dist) < max([(offset_vision_dist)*.25, 5.0])
  vel_sane = (abs(track.vRel + v_ego - lead.v[0]) < 10) or (v_ego + track.vRel > 3)
  if dist_sane and vel_sane:
    return track
  else:
    return None


def get_RadarState_from_vision(lead_msg: capnp._DynamicStructReader, v_ego: float, model_v_ego: float, lead_prob: float):
  lead_v_rel_pred = lead_msg.v[0] - model_v_ego
  return {
    "dRel": float(lead_msg.x[0] - RADAR_TO_CAMERA),
    "yRel": float(-lead_msg.y[0]),
    "vRel": float(lead_v_rel_pred),
    "vLead": float(v_ego + lead_v_rel_pred),
    "vLeadK": float(v_ego + lead_v_rel_pred),
    "aLeadK": float(lead_msg.a[0]),
    "aLeadTau": 0.3,
    "fcw": False,
    "modelProb": float(lead_prob),
    "status": True,
    "radar": False,
    "radarTrackId": -1,
  }


def get_lead(v_ego: float, ready: bool, tracks: dict[int, Track], lead_msg: capnp._DynamicStructReader,
             model_v_ego: float, lead_prob: float, low_speed_override: bool = True) -> dict[str, Any]:
  # Determine leads, this is where the essential logic happens
  if len(tracks) > 0 and ready and lead_prob > .5:
    track = match_vision_to_track(v_ego, lead_msg, tracks)
  else:
    track = None

  lead_dict = {'status': False}
  if track is not None:
    lead_dict = track.get_RadarState(lead_prob)
    lead_dict['visionMatched'] = True
    lead_dict['visionProbability'] = float(lead_msg.prob)
  elif (track is None) and ready and (lead_prob > .5):
    lead_dict = get_RadarState_from_vision(lead_msg, v_ego, model_v_ego, lead_prob)

  if low_speed_override:
    low_speed_tracks = [c for c in tracks.values() if c.potential_low_speed_lead(v_ego)]
    if len(low_speed_tracks) > 0:
      closest_track = min(low_speed_tracks, key=lambda c: c.dRel)

      # Only choose new track if it is actually closer than the previous one
      if (not lead_dict['status']) or (closest_track.dRel < lead_dict['dRel']):
        lead_dict = closest_track.get_RadarState()

  return lead_dict


class RadarD:
  def __init__(self, delay: float = 0.0):
    self.current_time = 0.0

    self.tracks: dict[int, Track] = {}
    self.kalman_params = KalmanParams(DT_MDL)
    self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

    self.v_ego = 0.0
    self.v_ego_hist = deque([0.0], maxlen=int(round(delay / DT_MDL))+1)
    self.last_v_ego_frame = -1

    self.radar_state: capnp._DynamicStructBuilder | None = None
    self.radar_state_valid = False

    self.ready = False

    # previous cycle's selected radarTrackId per lead slot, to detect a fresh lead-selection
    # transition -- the only time cutin_confidence() is worth evaluating (see update())
    self._prev_lead_track_id = [-1, -1]

    # (track_id, confidence, expires_mono) per lead slot, or None. SubMaster sockets are
    # conflated (only the latest message is kept) -- a consumer whose poll cadence lags
    # radard's publish for even one cycle could otherwise miss the single message carrying
    # a nonzero cutinConfidence entirely, since it's normally only set on the exact
    # transition cycle. Re-publishing the same value for a short window after the
    # transition, for as long as the same track stays selected, gives a lagging consumer a
    # second (or third) chance to see it. Safe against double-triggering: the consumer
    # (cutin_relaxation.py) only treats a *change* in lead_track_id as a new event, so
    # repeating the same (track_id, confidence) for several cycles is a no-op once it's
    # already been seen once.
    self._cutin_latch: list[tuple[int, float, float] | None] = [None, None]

  def update(self, sm: messaging.SubMaster, rr: car.RadarData):
    self.ready = sm.seen['modelV2']
    self.current_time = 1e-9*max(sm.logMonoTime.values())

    if sm.recv_frame['carState'] != self.last_v_ego_frame:
      self.v_ego = sm['carState'].vEgo
      self.v_ego_hist.append(self.v_ego)
      self.last_v_ego_frame = sm.recv_frame['carState']

    ar_pts = {pt.trackId: [pt.dRel, pt.yRel, pt.vRel, pt.measured] for pt in rr.points}

    # *** remove missing points from meta data ***
    for ids in list(self.tracks.keys()):
      if ids not in ar_pts:
        self.tracks.pop(ids, None)

    # *** compute the tracks ***
    for ids in ar_pts:
      rpt = ar_pts[ids]

      # align v_ego by a fixed time to align it with the radar measurement
      v_lead = rpt[2] + self.v_ego_hist[0]

      # create the track if it doesn't exist or it's a new track
      if ids not in self.tracks:
        self.tracks[ids] = Track(ids, v_lead, self.kalman_params)
      self.tracks[ids].update(rpt[0], rpt[1], rpt[2], v_lead, rpt[3])

    # *** publish radarState ***
    self.radar_state_valid = sm.all_checks()
    self.radar_state = log.RadarState.new_message()
    self.radar_state.mdMonoTime = sm.logMonoTime['modelV2']
    self.radar_state.radarErrors = rr.errors
    self.radar_state.carStateMonoTime = sm.logMonoTime['carState']

    if len(sm['modelV2'].velocity.x):
      model_v_ego = sm['modelV2'].velocity.x[0]
    else:
      model_v_ego = self.v_ego
    yaw_rate = sm['modelV2'].orientationRate.z[0] if len(sm['modelV2'].orientationRate.z) else 0.0
    straight_road = abs(yaw_rate) < CUTIN_MAX_YAW_RATE
    not_ego_lane_change = sm['modelV2'].meta.laneChangeState == log.LaneChangeState.off
    leads_v3 = sm['modelV2'].leadsV3
    if len(leads_v3) > 1:
      for i in range(2):
        # Asymmetric filter on lead prob to keep lead when uncertain
        lead_prob = leads_v3[i].prob
        if lead_prob > self.lead_prob_filters[i].x:
          self.lead_prob_filters[i].x = lead_prob
        else:
          self.lead_prob_filters[i].update(lead_prob)

      for i, lead in enumerate((self.radar_state.leadOne, self.radar_state.leadTwo)):
        values = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[i], model_v_ego,
                          self.lead_prob_filters[i].x, low_speed_override=(i == 0))

        track_id = values.get('radarTrackId', -1)
        is_fresh_selection = track_id != -1 and track_id != self._prev_lead_track_id[i]
        confidence = 0.0
        if is_fresh_selection and track_id in self.tracks:
          evidence = self.tracks[track_id].cutin_evidence(straight_road, not_ego_lane_change)
          confidence = evidence['confidence']
          # Log every transition, not just accepted ones -- an accepted-only log makes false
          # negatives ("that was obviously a cut-in, why did it score 0.31?") unanswerable
          # after a drive. accepted here matches the threshold cutin_relaxation.py itself uses.
          accepted = confidence > CUTIN_LOG_THRESHOLD
          msg = ('cut-in transition: track=%s accepted=%s confidence=%.2f age=%d age_ok=%s ' +
                 'recent_abs_yRel=%.2f inward_vy=%.2f straight_road=%s not_ego_lane_change=%s ' +
                 'dRel=%.1f vRel=%.1f v_ego=%.1f')
          cloudlog.info(msg, track_id, accepted, confidence, evidence['track_age_cycles'], evidence['age_ok'],
                        evidence['recent_abs_yRel'], evidence['inward_vy'], straight_road, not_ego_lane_change,
                        evidence['dRel'], evidence['vRel'], self.v_ego)
          if confidence > 0:
            self._cutin_latch[i] = (track_id, confidence, self.current_time + CUTIN_LATCH_DURATION)
        elif self._cutin_latch[i] is not None:
          latched_track_id, latched_confidence, expires = self._cutin_latch[i]
          if track_id == latched_track_id and self.current_time < expires:
            confidence = latched_confidence
          else:
            self._cutin_latch[i] = None
        values['cutinConfidence'] = confidence
        self._prev_lead_track_id[i] = track_id

        # pycapnp's dict-to-struct conversion creates schema reference cycles.
        # Real-time processes disable GC, so write scalar fields directly.
        # radar_state is fresh each update: absent fields keep their defaults.
        for key, value in values.items():
          setattr(lead, key, value)
        if lead.radar and lead.measured:
          lead.observationMonoTime = sm.logMonoTime.get('liveTracks', 0)

  def publish(self, pm: messaging.PubMaster):
    assert self.radar_state is not None

    radar_msg = messaging.new_message("radarState")
    radar_msg.valid = self.radar_state_valid
    radar_msg.radarState = self.radar_state
    pm.send("radarState", radar_msg)


# fuses camera and radar data for best lead detection
def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  # wait for stats about the car to come in from controls
  cloudlog.info("radard is waiting for CarParams")
  CP = messaging.log_from_bytes(Params().get("CarParams", block=True), car.CarParams)
  cloudlog.info("radard got CarParams")

  # *** setup messaging
  sm = messaging.SubMaster(['modelV2', 'carState', 'liveTracks'], poll='modelV2')
  pm = messaging.PubMaster(['radarState'])

  RD = RadarD(CP.radarDelay)

  while 1:
    sm.update()

    RD.update(sm, sm['liveTracks'])
    RD.publish(pm)


if __name__ == "__main__":
  main()
