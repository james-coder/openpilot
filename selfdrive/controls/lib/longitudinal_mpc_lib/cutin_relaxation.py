"""Post-cut-in comfort-spacing relaxation. Pure state machine, no casadi/ACADOS import,
same reason as gap_params.py's own docstring: long_mpc.py needs this logic AND the solver,
but this needs to be importable/unit-testable on its own.

After a validated cut-in (see selfdrive/controls/radard.py's cutin_confidence()), don't
immediately drive back to the full nominal comfort gap -- accept the actually-observed
headway (floored at CUTIN_STOP_DISTANCE_FLOOR/CUTIN_COMFORT_BRAKE_MAX below, not the
personality-validation bounds in opendbc.car.gm.volt_following) and blend back to nominal
over an adaptive recovery time, the way a human driver rebuilds spacing after someone
merges in -- faster if the merge felt tight/crowded, slower if it barely mattered.

Deliberately touches ONLY stop_distance and comfort_brake, never t_follow: long_mpc.py's
danger-zone constraint computes its own get_safe_obstacle_distance() using the live
t_follow param but ALWAYS the stock module-default stop_distance/comfort_brake, never the
per-cycle params[:,6]/[:,7] this module's output gets written into -- so this relaxation
does not directly weaken that constraint or the lead trajectory limits it enforces. It is
NOT a proof that nothing downstream is affected: FCW's planner_fcw path compares against
the MPC's own *solved trajectory* (self.x_sol), and changing the comfort cost changes that
trajectory, so relaxation can still indirectly shift when FCW's threshold is crossed. FCW
and hazardous-closing-speed behavior remain governed by their own regression-tested logic,
not by anything this module sets -- that's the actual backstop, not an assumption that this
module is causally inert. See /home/james/.claude/plans/memoized-puzzling-rose.md for the
full investigation and reasoning this relies on.

CUTIN_STOP_DISTANCE_FLOOR/CUTIN_COMFORT_BRAKE_MAX are deliberately wider than
opendbc.car.gm.volt_following's STOP_DISTANCE_BOUNDS/COMFORT_BRAKE_BOUNDS: those bounds
validate steady-state *personality* values, and "aggressive" already sits close to them by
design, leaving almost no real room to relax into (~11% of the nominal gap at 70mph,
confirmed by direct computation against MANUAL_INTERIM_PROFILE -- not enough to absorb a
real highway cut-in). Since the danger-zone constraint ignores stop_distance/comfort_brake
entirely regardless of value (previous paragraph), there is no extra collision-safety cost
to a wider relaxation-only floor -- only "don't feed the MPC cost function something
pathological" matters here, not a safety bound.
"""

from dataclasses import dataclass

from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.gap_params import get_safe_obstacle_distance

CUTIN_STOP_DISTANCE_FLOOR = 4.5   # m -- stock openpilot's own original floor, before this
                                   # driver's steady-state personal minimum bump; still a
                                   # real, previously-considered-acceptable value, not new
CUTIN_COMFORT_BRAKE_MAX = 25.0    # shapes the comfort-cost curve only; never touches
                                   # accel/jerk limits or the danger-zone constraint
MIN_RECOVERY_TIME_S = 8.0     # most severe cut-ins (frac~1): recover this fast
MAX_RECOVERY_TIME_S = 25.0    # mildest cut-ins (frac~0): take this long, smoothly
MAX_RELAXATION_TIME_S = 60.0  # cap on a continuous streak of repeated cut-ins resetting the timer
CONFIDENCE_THRESHOLD = 0.5    # cutin_confidence required to (re)start relaxation
MIN_SPEED = 12.0               # m/s (~27 mph); relaxation only engages above this -- low speed
                                # is already governed by the existing stop_trajectory/
                                # personal_curve system, which takes precedence regardless
_BISECTION_ITERS = 20


@dataclass(frozen=True)
class CutinRelaxationState:
  active_track_id: int = -1
  streak_started_mono: float = -1.    # start of this streak of repeated cut-ins, for MAX_RELAXATION_TIME_S
  recovery_started_mono: float = -1.  # start of the CURRENT recovery leg
  recovery_time: float = MAX_RECOVERY_TIME_S  # this event's own recovery duration (adaptive, see module docstring)
  start_frac: float = 0.              # 0 = no relaxation, 1 = pinned at the floor/max


def _gap_at_frac(frac: float, v_ego: float, t_follow: float,
                  nominal_stop_distance: float, nominal_comfort_brake: float) -> tuple[float, float, float]:
  """(gap, stop_distance, comfort_brake) at a given relaxation fraction. Continuous and
  monotonically non-increasing in frac: both terms shrink the gap as frac rises."""
  stop_distance = nominal_stop_distance - frac * (nominal_stop_distance - CUTIN_STOP_DISTANCE_FLOOR)
  comfort_brake = nominal_comfort_brake + frac * (CUTIN_COMFORT_BRAKE_MAX - nominal_comfort_brake)
  gap = get_safe_obstacle_distance(v_ego, t_follow, stop_distance, comfort_brake)
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


def update(state: CutinRelaxationState, now: float, v_ego: float, lead_status: bool,
           cutin_confidence: float, lead_track_id: int, observed_d_rel: float,
           nominal_t_follow: float, nominal_comfort_brake: float,
           nominal_stop_distance: float) -> tuple[CutinRelaxationState, float, float]:
  """One call per LongitudinalMpc.update() cycle, right after resolve_gap_params(). Returns
  (new_state, stop_distance, comfort_brake) -- t_follow is passed through unchanged by the
  caller; this only ever supplies (possibly relaxed) stop_distance/comfort_brake."""
  if v_ego < MIN_SPEED or not lead_status:
    return CutinRelaxationState(), nominal_stop_distance, nominal_comfort_brake

  recovering = state.active_track_id != -1 and now - state.recovery_started_mono < state.recovery_time
  streak_elapsed = now - state.streak_started_mono if recovering else 0.
  is_new_cutin = cutin_confidence >= CONFIDENCE_THRESHOLD and lead_track_id != state.active_track_id

  if is_new_cutin and streak_elapsed < MAX_RELAXATION_TIME_S:
    floor_gap, _, _ = _gap_at_frac(1., v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    current_frac = _current_frac(state, now) if recovering else 0.
    current_gap, _, _ = _gap_at_frac(current_frac, v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    # Never ask for MORE gap than what's already the current target (repeated cut-ins keep
    # prior progress, they don't loosen it back up), and never below the floor.
    target_gap = min(max(observed_d_rel, floor_gap), current_gap)
    start_frac = _solve_frac(target_gap, v_ego, nominal_t_follow, nominal_stop_distance, nominal_comfort_brake)
    streak_start = state.streak_started_mono if recovering else now
    recovery_time = MAX_RECOVERY_TIME_S - start_frac * (MAX_RECOVERY_TIME_S - MIN_RECOVERY_TIME_S)
    new_state = CutinRelaxationState(lead_track_id, streak_start, now, recovery_time, start_frac)
  elif recovering:
    new_state = state
  else:
    return CutinRelaxationState(), nominal_stop_distance, nominal_comfort_brake

  _, stop_distance, comfort_brake = _gap_at_frac(_current_frac(new_state, now), v_ego, nominal_t_follow,
                                                  nominal_stop_distance, nominal_comfort_brake)
  return new_state, stop_distance, comfort_brake
