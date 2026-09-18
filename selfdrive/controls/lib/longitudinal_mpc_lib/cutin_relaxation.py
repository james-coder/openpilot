"""Post-cut-in comfort-spacing relaxation. Pure state machine, no casadi/ACADOS import,
same reason as gap_params.py's own docstring: long_mpc.py needs this logic AND the solver,
but this needs to be importable/unit-testable on its own.

After a validated cut-in (see selfdrive/controls/radard.py's cutin_confidence()), don't
immediately drive back to the full nominal comfort gap -- accept the actually-observed
headway (floored at the existing STOP_DISTANCE_BOUNDS) and blend linearly back to nominal
over RECOVERY_TIME_S, the way a human driver rebuilds spacing after someone merges in.

Deliberately touches ONLY stop_distance, never t_follow or comfort_brake:
long_mpc.py's danger-zone constraint (the only safety-adjacent term in the OCP) computes
its own get_safe_obstacle_distance() using the live t_follow param but ALWAYS the stock
module-default stop_distance/comfort_brake, never the per-cycle params[:,6]/[:,7] this
module's output gets written into. So relaxing stop_distance alone cannot loosen that
constraint at all, by construction -- no extra safety plumbing required. See
/home/james/.claude/plans/memoized-puzzling-rose.md for the full investigation this relies on.
"""

from dataclasses import dataclass

from opendbc.car.gm.volt_following import STOP_DISTANCE_BOUNDS

RECOVERY_TIME_S = 20.0         # linear stop_distance recovery time after a validated cut-in
MAX_RELAXATION_TIME_S = 60.0   # cap on a continuous streak of repeated cut-ins resetting the timer
CONFIDENCE_THRESHOLD = 0.5     # cutin_confidence required to (re)start relaxation
MIN_SPEED = 12.0                # m/s (~27 mph); relaxation only engages above this -- low speed
                                 # is already governed by the existing stop_trajectory/
                                 # personal_curve system, which takes precedence regardless


@dataclass(frozen=True)
class CutinRelaxationState:
  active_track_id: int = -1
  streak_started_mono: float = -1.    # start of this streak of repeated cut-ins, for MAX_RELAXATION_TIME_S
  recovery_started_mono: float = -1.  # start of the CURRENT recovery leg, for the alpha ramp
  start_stop_distance: float = 0.


def _effective_stop_distance(state: CutinRelaxationState, now: float, nominal_stop_distance: float) -> float:
  alpha = min(max((now - state.recovery_started_mono) / RECOVERY_TIME_S, 0.), 1.)
  return state.start_stop_distance + alpha * (nominal_stop_distance - state.start_stop_distance)


def _relaxed_start_stop_distance(observed_d_rel: float, v_ego: float, t_follow: float,
                                  comfort_brake: float, fallback_stop_distance: float) -> float:
  # Solve get_safe_obstacle_distance(v_ego, t_follow, stop_distance, comfort_brake) == observed_d_rel
  # for stop_distance: request just enough forgiveness to match what's actually there right now.
  deficit_free = observed_d_rel - t_follow * v_ego - (v_ego ** 2) / (2 * comfort_brake)
  # Never ask for MORE gap than the current target (a cut-in should only ever shrink the ask),
  # and never below the shared, already-validated floor.
  return float(min(max(deficit_free, STOP_DISTANCE_BOUNDS[0]), fallback_stop_distance))


def update(state: CutinRelaxationState, now: float, v_ego: float, lead_status: bool,
           cutin_confidence: float, lead_track_id: int, observed_d_rel: float,
           nominal_t_follow: float, nominal_comfort_brake: float,
           nominal_stop_distance: float) -> tuple[CutinRelaxationState, float]:
  """One call per LongitudinalMpc.update() cycle, right after resolve_gap_params(). Returns
  (new_state, stop_distance) -- comfort_brake/t_follow are passed through unchanged by the
  caller; this only ever supplies a (possibly relaxed) stop_distance."""
  if v_ego < MIN_SPEED or not lead_status:
    return CutinRelaxationState(), nominal_stop_distance

  recovering = state.active_track_id != -1 and now - state.recovery_started_mono < RECOVERY_TIME_S
  streak_elapsed = now - state.streak_started_mono if recovering else 0.
  is_new_cutin = cutin_confidence >= CONFIDENCE_THRESHOLD and lead_track_id != state.active_track_id

  if is_new_cutin and streak_elapsed < MAX_RELAXATION_TIME_S:
    current_stop = _effective_stop_distance(state, now, nominal_stop_distance) if recovering else nominal_stop_distance
    streak_start = state.streak_started_mono if recovering else now
    start_stop = _relaxed_start_stop_distance(observed_d_rel, v_ego, nominal_t_follow, nominal_comfort_brake, current_stop)
    new_state = CutinRelaxationState(lead_track_id, streak_start, now, start_stop)
  elif recovering:
    new_state = state
  else:
    return CutinRelaxationState(), nominal_stop_distance

  return new_state, _effective_stop_distance(new_state, now, nominal_stop_distance)
