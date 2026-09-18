"""Post-cut-in comfort-spacing relaxation. Pure state machine, no casadi/ACADOS import:
long_mpc.py needs this logic AND the solver, but this needs to be importable/unit-testable
on its own (the device cannot build ACADOS, so long_mpc.py itself cannot be imported here).

After a validated cut-in (see selfdrive/controls/radard.py's cutin_confidence()), don't
immediately drive back to the full nominal comfort gap -- accept the actually-observed
headway (floored at CUTIN_STOP_DISTANCE_FLOOR/CUTIN_COMFORT_BRAKE_MAX below) and blend back
to nominal over an adaptive recovery time, the way a human driver rebuilds spacing after
someone merges in -- faster if the merge felt tight/crowded, slower if it barely mattered.

Deliberately touches ONLY stop_distance and comfort_brake, never t_follow.

THE ONE INVARIANT THIS MODULE MUST HOLD (overspeed root cause, v2 -> v3):
  Relaxation changes exactly one thing -- the effective (stop_distance, comfort_brake) pair --
  and EVERY consumer inside LongitudinalMpc.update() must see that same pair. In v2 the relaxed
  pair was written into params[:,6]/[:,7] (the comfort-cost target) only, while the fake
  cruise obstacle was still placed using the NOMINAL pair. The cruise obstacle therefore sat
  farther away than the target the cost was pulling ego toward, so the cost happily
  accelerated ego past v_cruise to "close" that gap: 3-8 mph over the set speed after every
  cut-in. gap_targets() below is the single place that turns the effective pair into the
  cruise-obstacle gap, the cost-target params and the danger-factor scale, so they cannot
  disagree again. test_cutin_relaxation.py::TestOverspeedGuard pins this.

CUTIN_STOP_DISTANCE_FLOOR == the current nominal STOP_DISTANCE (4.5), so stop_distance no
longer moves at all; only comfort_brake is relaxed. CUTIN_COMFORT_BRAKE_MAX is chosen so that
at 70 mph (31.29 m/s, standard t_follow 0.725, nominal 4.5/6.0) the fully relaxed floor gap is
~55% of the nominal gap: nominal = 31.29^2/12 + 0.725*31.29 + 4.5 = 108.8 m, target 59.8 m,
=> 31.29^2 / (2 * (59.8 - 22.7 - 4.5)) = 14.997 -> 15.0 (floor gap = 54.99% of nominal).
This replaces v2's 25.0, which was sized against the old 2.5 comfort_brake and is far more
than is needed to absorb a realistic highway gap loss now that nominal is already 6.0.

ROBUSTNESS (v3, from the recorded drive where v2 misbehaved):
  * Lead flicker: leadOne.status dropped for a single radar frame mid-event, which reset the
    state; radard's ~1 s confidence latch then re-triggered a brand new event 0.1-0.3 s later
    ("started"/"ended" thrash in the logs). An active relaxation now survives lead loss for
    LEAD_LOSS_HOLD_S before it is dropped. The speed gate stays immediate.
  * Same-track cooldown: after an event ends, the same radar track cannot start another one
    for SAME_TRACK_COOLDOWN_S.
  * No-op guard: if the observed gap is already >= the current target (solved start fraction
    < NOOP_FRAC -- v2 "accepted" cut-ins 335-378 ft ahead), no event is created and nothing
    is logged.
"""

from dataclasses import dataclass, replace

import numpy as np

CUTIN_STOP_DISTANCE_FLOOR = 4.5   # m == nominal STOP_DISTANCE in long_mpc.py: stop_distance does not move
CUTIN_COMFORT_BRAKE_MAX = 15.0    # see module docstring for the 55%-of-nominal-at-70mph derivation
MIN_RECOVERY_TIME_S = 8.0     # most severe cut-ins (frac~1): recover this fast
MAX_RECOVERY_TIME_S = 25.0    # mildest cut-ins (frac~0): take this long, smoothly
MAX_RELAXATION_TIME_S = 60.0  # cap on a continuous streak of repeated cut-ins resetting the timer
CONFIDENCE_THRESHOLD = 0.5    # cutin_confidence required to (re)start relaxation
LEAD_LOSS_HOLD_S = 0.5        # keep an active relaxation through this much lead-status dropout
SAME_TRACK_COOLDOWN_S = 2.0   # a track that just finished an event cannot start another for this long
NOOP_FRAC = 0.05              # solved start fraction below this = gap already fine, do nothing
MIN_SPEED = 12.0               # m/s (~27 mph); relaxation only engages above this -- low speed
                                # is already governed by the existing stop_trajectory/
                                # personal_curve system, which takes precedence regardless
_BISECTION_ITERS = 20


def safe_obstacle_distance(v_ego, t_follow, stop_distance, comfort_brake):
  """Same formula as long_mpc.get_safe_obstacle_distance (which delegates here), kept in this
  ACADOS-free module so the relaxation and its tests can use it without importing the solver."""
  return (v_ego**2) / (2 * comfort_brake) + t_follow * v_ego + stop_distance


@dataclass(frozen=True)
class CutinRelaxationState:
  active_track_id: int = -1
  streak_started_mono: float = -1.    # start of this streak of repeated cut-ins, for MAX_RELAXATION_TIME_S
  recovery_started_mono: float = -1.  # start of the CURRENT recovery leg
  recovery_time: float = MAX_RECOVERY_TIME_S  # this event's own recovery duration (adaptive, see module docstring)
  start_frac: float = 0.              # 0 = no relaxation, 1 = pinned at the floor/max
  lead_lost_since: float = -1.        # mono time leadOne.status first went False during this event, else -1
  cooldown_track_id: int = -1         # track whose event just ended (same-track re-trigger suppression)
  cooldown_until: float = -1.         # mono time that suppression expires


@dataclass(frozen=True)
class GapTargets:
  """Everything LongitudinalMpc.update() derives from the effective (stop_distance,
  comfort_brake) pair, computed from that ONE pair so no consumer can drift from the others."""
  stop_distance: float
  comfort_brake: float
  cruise_gap: np.ndarray    # safe distance at each horizon node's clipped cruise speed -> cruise obstacle
  comfort_now: float        # safe distance at the current v_ego -> the live comfort target
  danger_factor: float      # params[:,5]: LEAD_DANGER_FACTOR scaled so the compiled zone tracks comfort_now


def gap_targets(v_ego: float, v_cruise_clipped, t_follow: float, stop_distance: float, comfort_brake: float,
                baked_stop_distance: float, baked_comfort_brake: float, lead_danger_factor: float) -> GapTargets:
  cruise_gap = safe_obstacle_distance(v_cruise_clipped, t_follow, stop_distance, comfort_brake)
  comfort_now = safe_obstacle_distance(v_ego, t_follow, stop_distance, comfort_brake)
  baked_safety = safe_obstacle_distance(v_ego, t_follow, baked_stop_distance, baked_comfort_brake)
  danger_factor = lead_danger_factor * min(1.0, comfort_now / max(baked_safety, 1e-3))
  return GapTargets(stop_distance, comfort_brake, cruise_gap, comfort_now, danger_factor)


def _gap_at_frac(frac: float, v_ego: float, t_follow: float,
                  nominal_stop_distance: float, nominal_comfort_brake: float) -> tuple[float, float, float]:
  """(gap, stop_distance, comfort_brake) at a given relaxation fraction. Continuous and
  monotonically non-increasing in frac: both terms shrink (or hold) the gap as frac rises."""
  stop_distance = nominal_stop_distance - frac * (nominal_stop_distance - CUTIN_STOP_DISTANCE_FLOOR)
  comfort_brake = nominal_comfort_brake + frac * (CUTIN_COMFORT_BRAKE_MAX - nominal_comfort_brake)
  gap = safe_obstacle_distance(v_ego, t_follow, stop_distance, comfort_brake)
  return gap, stop_distance, comfort_brake


def _solve_frac(target_gap: float, v_ego: float, t_follow: float,
                 nominal_stop_distance: float, nominal_comfort_brake: float) -> float:
  """frac in [0,1] such that _gap_at_frac(frac, ...)[0] == target_gap, by bisection --
  gap_at_frac is monotonic so this always converges, unlike a closed-form single-variable
  inversion once two variables are involved."""
  lo, hi = 0., 1.
  for _ in range(_BISECTION_ITERS):
    mid = (lo + hi) / 2.
    gap, _, _ = _gap_at_frac(mid, v_ego, t_follow, nominal_stop_distance, nominal_comfort_brake)
    if gap > target_gap:
      lo = mid
    else:
      hi = mid
  return hi


def _current_frac(state: CutinRelaxationState, now: float) -> float:
  alpha = min(max((now - state.recovery_started_mono) / state.recovery_time, 0.), 1.)
  return state.start_frac * (1. - alpha)


def _idle(state: CutinRelaxationState) -> CutinRelaxationState:
  """No active event; only the same-track cooldown survives."""
  return CutinRelaxationState(cooldown_track_id=state.cooldown_track_id, cooldown_until=state.cooldown_until)


def _end_event(state: CutinRelaxationState, now: float) -> CutinRelaxationState:
  return CutinRelaxationState(cooldown_track_id=state.active_track_id, cooldown_until=now + SAME_TRACK_COOLDOWN_S)


def update(state: CutinRelaxationState, now: float, v_ego: float, lead_status: bool,
           cutin_confidence: float, lead_track_id: int, observed_d_rel: float,
           nominal_t_follow: float, nominal_comfort_brake: float,
           nominal_stop_distance: float) -> tuple[CutinRelaxationState, float, float]:
  """One call per LongitudinalMpc.update() cycle, BEFORE the cruise obstacle is built. Returns
  (new_state, stop_distance, comfort_brake) -- the effective pair every consumer must then use;
  t_follow is passed through unchanged by the caller."""
  if v_ego < MIN_SPEED:
    return CutinRelaxationState(), nominal_stop_distance, nominal_comfort_brake

  active = state.active_track_id != -1
  recovering = active and now - state.recovery_started_mono < state.recovery_time

  if not lead_status:
    if not recovering:
      return (_end_event(state, now) if active else _idle(state)), nominal_stop_distance, nominal_comfort_brake
    lost_since = state.lead_lost_since if state.lead_lost_since >= 0. else now
    if now - lost_since >= LEAD_LOSS_HOLD_S:
      return _end_event(state, now), nominal_stop_distance, nominal_comfort_brake
    held = replace(state, lead_lost_since=lost_since)
    _, stop_distance, comfort_brake = _gap_at_frac(_current_frac(held, now), v_ego, nominal_t_follow,
                                                    nominal_stop_distance, nominal_comfort_brake)
    return held, stop_distance, comfort_brake

  if recovering and state.lead_lost_since >= 0.:
    state = replace(state, lead_lost_since=-1.)

  streak_elapsed = now - state.streak_started_mono if recovering else 0.
  in_cooldown = lead_track_id == state.cooldown_track_id and now < state.cooldown_until
  is_new_cutin = (cutin_confidence >= CONFIDENCE_THRESHOLD and lead_track_id != state.active_track_id
                  and not in_cooldown)

  if is_new_cutin and streak_elapsed < MAX_RELAXATION_TIME_S:
    floor_gap, _, _ = _gap_at_frac(1., v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    current_frac = _current_frac(state, now) if recovering else 0.
    current_gap, _, _ = _gap_at_frac(current_frac, v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    # Never ask for MORE gap than what's already the current target (repeated cut-ins keep
    # prior progress, they don't loosen it back up), and never below the floor.
    target_gap = min(max(observed_d_rel, floor_gap), current_gap)
    start_frac = _solve_frac(target_gap, v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    if observed_d_rel >= current_gap or start_frac < NOOP_FRAC:
      # Gap already at/above the current target: nothing to relax. Keep whatever was recovering, else stay idle.
      new_state = state if recovering else _idle(state)
    else:
      streak_start = state.streak_started_mono if recovering else now
      recovery_time = MAX_RECOVERY_TIME_S - start_frac * (MAX_RECOVERY_TIME_S - MIN_RECOVERY_TIME_S)
      new_state = CutinRelaxationState(lead_track_id, streak_start, now, recovery_time, start_frac)
  elif recovering:
    new_state = state
  else:
    return (_end_event(state, now) if active else _idle(state)), nominal_stop_distance, nominal_comfort_brake

  if new_state.active_track_id == -1:
    return new_state, nominal_stop_distance, nominal_comfort_brake
  _, stop_distance, comfort_brake = _gap_at_frac(_current_frac(new_state, now), v_ego, nominal_t_follow,
                                                  nominal_stop_distance, nominal_comfort_brake)
  return new_state, stop_distance, comfort_brake
