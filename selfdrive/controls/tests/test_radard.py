"""Cut-in detection: Track.cutin_confidence() in isolation, and RadarD's transition-detection
wiring end to end. See selfdrive/controls/radard.py and
/home/james/.claude/plans/memoized-puzzling-rose.md for the design this implements."""
import pytest
from cereal import log

from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.radard import (
  RadarD, Track, KalmanParams,
  CUTIN_ADJACENCY_DECAY_TAU, CUTIN_LATCH_DURATION, CUTIN_MIN_TRACK_AGE_CYCLES, CUTIN_LOG_THRESHOLD,
)
from openpilot.selfdrive.controls.tests.test_radar_memory import RadarInputs

KP = KalmanParams(DT_MDL)


def make_track(identifier=1):
  return Track(identifier, v_lead=15., kalman_params=KP)


def feed_ramp(track, cycles, y_start, y_end, d_rel=25., v_rel=0., measured=True):
  """Drive `cycles` updates with yRel linearly ramping from y_start to y_end."""
  for i in range(cycles):
    y = y_start + (y_end - y_start) * i / max(cycles - 1, 1)
    track.update(d_rel, y, v_rel, 15. + v_rel, measured)
  return track


class TestTrackCutinConfidence:
  def test_genuine_cutin_scores_high(self):
    track = feed_ramp(make_track(), cycles=60, y_start=3.0, y_end=0.2)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=True) > 0.8

  def test_adjacent_vehicle_that_never_merges_scores_low(self):
    # small wander, no sustained inward trend
    track = feed_ramp(make_track(), cycles=60, y_start=3.0, y_end=2.8)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=True) < 0.1

  def test_never_adjacent_scores_zero_regardless_of_motion(self):
    # was always basically in-path -- e.g. a lead-selection swap between two in-path tracks
    track = feed_ramp(make_track(), cycles=60, y_start=0.3, y_end=0.0)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=True) == 0.

  def test_insufficient_history_scores_zero(self):
    track = feed_ramp(make_track(), cycles=CUTIN_MIN_TRACK_AGE_CYCLES - 1, y_start=3.0, y_end=0.1)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=True) == 0.

  def test_curved_road_gate_suppresses_confidence(self):
    track = feed_ramp(make_track(), cycles=60, y_start=3.0, y_end=0.2)
    assert track.cutin_confidence(straight_road=False, not_ego_lane_change=True) == 0.

  def test_ego_lane_change_gate_suppresses_confidence(self):
    track = feed_ramp(make_track(), cycles=60, y_start=3.0, y_end=0.2)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=False) == 0.

  def test_outward_motion_scores_zero(self):
    # vehicle drifting away from ego's path, not toward it
    track = feed_ramp(make_track(), cycles=60, y_start=0.5, y_end=3.0)
    assert track.cutin_confidence(straight_road=True, not_ego_lane_change=True) == 0.


class TestRecentAdjacencyDecay:
  """Regression coverage for the fix this round's review caught: a track's adjacency evidence
  must decay after it returns to in-path, not stay lifetime-sticky (the old peak_abs_yRel
  monotonic max never went back down, so a car that ran parallel long ago and has been
  in-path ever since could still misfire on a much-later, unrelated lead-selection swap)."""

  def test_recent_abs_yrel_decays_after_returning_to_path(self):
    track = feed_ramp(make_track(), cycles=CUTIN_MIN_TRACK_AGE_CYCLES, y_start=3.0, y_end=3.0)
    assert track.recent_abs_yRel.x == pytest.approx(3.0, abs=0.1)
    # settle in-path for far longer than the decay tau
    settle_cycles = int(CUTIN_ADJACENCY_DECAY_TAU * 4 / DT_MDL)
    feed_ramp(track, cycles=settle_cycles, y_start=0.0, y_end=0.0)
    assert track.recent_abs_yRel.x < 0.5


class TestRadarDCutinWiring:
  def test_existing_lead_braking_never_flags_a_cutin(self):
    # same trackId persists as leadOne the whole time -- dRel shrinking is lead braking,
    # not a cut-in, and there's never a selection transition to evaluate
    sm, rd = RadarInputs(), RadarD()
    for cycle in range(30):
      sm.recv_frame['carState'] += 1
      sm.rr.points[0].dRel = 25. - cycle * 0.3
      rd.update(sm, sm.rr.as_reader())
      assert rd.radar_state.leadOne.cutinConfidence == 0.

  def test_lead_selection_swap_between_in_path_tracks_scores_low(self):
    # two tracks, both always near yRel=0 (both in-path) -- swapping which one is leadOne
    # is a selection change but not a cut-in
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 2)
    sm.rr.points[0].trackId, sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].measured = 1, 30., 0.1, True
    sm.rr.points[1].trackId, sm.rr.points[1].dRel, sm.rr.points[1].yRel, sm.rr.points[1].measured = 2, 25., -0.1, True
    for _cycle in range(CUTIN_MIN_TRACK_AGE_CYCLES + 5):
      sm.recv_frame['carState'] += 1
      # model lead tracks whichever radar point is currently closer, so selection swaps
      # once track 2 (already closer) is favored by the vision match every cycle
      sm.model.leadsV3[0].x = [25. + 1.52]
      sm.model.leadsV3[0].y = [0.1]
      rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.radarTrackId == 2
    assert rd.radar_state.leadOne.cutinConfidence < 0.1

  def test_genuine_lateral_cutin_is_flagged_once_vision_confirms_it(self):
    # Radar sees an adjacent-lane track drifting inward for a while before the vision model
    # (and hence lead selection) catches up to it -- mirrors real vision lag.
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 1)
    sm.rr.points[0].trackId = 7
    sm.rr.points[0].measured = True
    build_cycles = CUTIN_MIN_TRACK_AGE_CYCLES + 20
    for i in range(build_cycles):
      sm.recv_frame['carState'] += 1
      y = 3.0 + (0.2 - 3.0) * i / (build_cycles - 1)
      sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., y, 0.
      sm.model.leadsV3[0].prob = 0.  # vision hasn't matched this track yet
      rd.update(sm, sm.rr.as_reader())
      assert rd.radar_state.leadOne.status is False

    # vision now confirms the same physical position/speed -- lead selection transitions
    sm.recv_frame['carState'] += 1
    sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., 0.15, 0.
    sm.model.leadsV3[0].prob = 0.9
    sm.model.leadsV3[0].x = [25. + 1.52]
    sm.model.leadsV3[0].y = [-0.15]
    sm.model.leadsV3[0].v = [15.]
    rd.update(sm, sm.rr.as_reader())

    assert rd.radar_state.leadOne.radarTrackId == 7
    first_confidence = rd.radar_state.leadOne.cutinConfidence
    assert first_confidence > CUTIN_LOG_THRESHOLD

    # next cycle: same track remains leadOne -- no longer a *fresh* selection, but the
    # confidence stays latched at the same value for a short window (see CUTIN_LATCH_DURATION)
    # so a consumer that missed the single transition cycle (conflated pub/sub sockets only
    # keep the latest message) still gets a chance to see it.
    sm.recv_frame['carState'] += 1
    rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.radarTrackId == 7
    assert rd.radar_state.leadOne.cutinConfidence == pytest.approx(first_confidence)

    # once the latch window elapses, it drops back to 0 (no re-trigger, just stops repeating).
    # RadarInputs' logMonoTime is static by default (fine for the other tests here, which
    # don't care about elapsed wall time) -- advance it here since the latch's expiry is
    # driven by real elapsed time, not cycle count.
    for _ in range(int(CUTIN_LATCH_DURATION / DT_MDL) + 5):
      sm.recv_frame['carState'] += 1
      sm.logMonoTime['modelV2'] += int(DT_MDL * 1e9)
      rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.cutinConfidence == 0.

  def test_curved_road_suppresses_a_real_cutin_signature(self):
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 1)
    sm.rr.points[0].trackId = 7
    sm.rr.points[0].measured = True
    sm.model.orientationRate.z = [0.2]  # above CUTIN_MAX_YAW_RATE
    build_cycles = CUTIN_MIN_TRACK_AGE_CYCLES + 20
    for i in range(build_cycles):
      sm.recv_frame['carState'] += 1
      y = 3.0 + (0.2 - 3.0) * i / (build_cycles - 1)
      sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., y, 0.
      sm.model.leadsV3[0].prob = 0.
      rd.update(sm, sm.rr.as_reader())

    sm.recv_frame['carState'] += 1
    sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., 0.15, 0.
    sm.model.leadsV3[0].prob = 0.9
    sm.model.leadsV3[0].x = [25. + 1.52]
    sm.model.leadsV3[0].y = [-0.15]
    sm.model.leadsV3[0].v = [15.]
    rd.update(sm, sm.rr.as_reader())

    assert rd.radar_state.leadOne.radarTrackId == 7
    assert rd.radar_state.leadOne.cutinConfidence == 0.

  def test_ego_lane_change_suppresses_a_real_cutin_signature(self):
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 1)
    sm.rr.points[0].trackId = 7
    sm.rr.points[0].measured = True
    sm.model.meta.laneChangeState = log.LaneChangeState.laneChangeStarting
    build_cycles = CUTIN_MIN_TRACK_AGE_CYCLES + 20
    for i in range(build_cycles):
      sm.recv_frame['carState'] += 1
      y = 3.0 + (0.2 - 3.0) * i / (build_cycles - 1)
      sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., y, 0.
      sm.model.leadsV3[0].prob = 0.
      rd.update(sm, sm.rr.as_reader())

    sm.recv_frame['carState'] += 1
    sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., 0.15, 0.
    sm.model.leadsV3[0].prob = 0.9
    sm.model.leadsV3[0].x = [25. + 1.52]
    sm.model.leadsV3[0].y = [-0.15]
    sm.model.leadsV3[0].v = [15.]
    rd.update(sm, sm.rr.as_reader())

    assert rd.radar_state.leadOne.radarTrackId == 7
    assert rd.radar_state.leadOne.cutinConfidence == 0.


class TestCutinConfidenceLatch:
  """Regression coverage for the fix this round's review caught: SubMaster sockets are
  conflated (only the latest message survives), so a consumer that misses the single cycle
  a fresh cut-in transition fires on would otherwise lose the signal entirely. The latch
  re-publishes the same (track_id, confidence) for a short window so a lagging consumer
  gets more than one chance to see it, without risking a double-trigger (cutin_relaxation.py
  only reacts to lead_track_id actually *changing*, so repeating the same value is a no-op
  once it's already been consumed once)."""

  def _build_and_confirm_cutin(self, sm, rd, track_id=7):
    build_cycles = CUTIN_MIN_TRACK_AGE_CYCLES + 20
    for i in range(build_cycles):
      sm.recv_frame['carState'] += 1
      y = 3.0 + (0.2 - 3.0) * i / (build_cycles - 1)
      sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., y, 0.
      sm.model.leadsV3[0].prob = 0.
      rd.update(sm, sm.rr.as_reader())
    sm.recv_frame['carState'] += 1
    sm.rr.points[0].dRel, sm.rr.points[0].yRel, sm.rr.points[0].vRel = 25., 0.15, 0.
    sm.model.leadsV3[0].prob = 0.9
    sm.model.leadsV3[0].x = [25. + 1.52]
    sm.model.leadsV3[0].y = [-0.15]
    sm.model.leadsV3[0].v = [15.]
    rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.radarTrackId == track_id
    assert rd.radar_state.leadOne.cutinConfidence > CUTIN_LOG_THRESHOLD

  def test_latch_survives_several_missed_consumer_reads(self):
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 1)
    sm.rr.points[0].trackId = 7
    sm.rr.points[0].measured = True
    self._build_and_confirm_cutin(sm, rd)
    confidence = rd.radar_state.leadOne.cutinConfidence

    # simulate a consumer that only reads every 3rd radarState message -- as long as it reads
    # any one of them within the latch window, it must still see the same confidence
    for _ in range(3):
      sm.recv_frame['carState'] += 1
      rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.cutinConfidence == pytest.approx(confidence)

  def test_a_different_track_selected_clears_the_latch_immediately(self):
    sm, rd = RadarInputs(), RadarD()
    sm.rr.init('points', 2)
    sm.rr.points[0].trackId = 7
    sm.rr.points[0].measured = True
    sm.rr.points[1].trackId = 9
    sm.rr.points[1].dRel, sm.rr.points[1].yRel, sm.rr.points[1].vRel, sm.rr.points[1].measured = 40., 0.0, 0., True
    self._build_and_confirm_cutin(sm, rd)

    # a different track becomes leadOne before the latch would naturally expire -- the stale
    # latched confidence must not leak onto this unrelated selection
    sm.recv_frame['carState'] += 1
    sm.model.leadsV3[0].x = [40. + 1.52]
    sm.model.leadsV3[0].y = [0.0]
    sm.model.leadsV3[0].v = [15.]
    rd.update(sm, sm.rr.as_reader())
    assert rd.radar_state.leadOne.radarTrackId == 9
    assert rd.radar_state.leadOne.cutinConfidence == 0.
