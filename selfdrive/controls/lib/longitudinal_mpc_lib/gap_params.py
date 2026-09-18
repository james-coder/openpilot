"""Personality/following-gap math, kept free of casadi/ACADOS imports on purpose.

long_mpc.py imports everything here (it needs the ACADOS solver too and can't be
imported standalone in an environment without a built c_generated_code); this module
exists so the same logic is importable and unit-testable on its own.
"""

from cereal import log

COMFORT_BRAKE = 2.5
STOP_DISTANCE = 6.0


def get_jerk_factor(personality=log.LongitudinalPersonality.standard):
  if personality == log.LongitudinalPersonality.relaxed:
    return 1.0
  elif personality == log.LongitudinalPersonality.standard:
    return 1.0
  elif personality == log.LongitudinalPersonality.aggressive:
    return 0.5
  else:
    raise NotImplementedError("Longitudinal personality not supported")


def get_T_FOLLOW(personality=log.LongitudinalPersonality.standard):
  if personality == log.LongitudinalPersonality.relaxed:
    return 1.75
  elif personality == log.LongitudinalPersonality.standard:
    return 1.45
  elif personality == log.LongitudinalPersonality.aggressive:
    return 1.25
  else:
    raise NotImplementedError("Longitudinal personality not supported")


def get_stopped_equivalence_factor(v_lead):
  return (v_lead**2) / (2 * COMFORT_BRAKE)


def get_safe_obstacle_distance(v_ego, t_follow, stop_distance=STOP_DISTANCE, comfort_brake=COMFORT_BRAKE):
  return (v_ego**2) / (2 * comfort_brake) + t_follow * v_ego + stop_distance


def resolve_gap_params(personality, following_profile, stop_distance, comfort_brake):
  """Pure function, testable without a solver: (t_follow, comfort_brake, stop_distance)
  for this personality. following_profile=None (the default for every car, and for the
  Volt until a validated fit exists) reproduces exactly the stock lookup — get_T_FOLLOW()
  plus whatever stop_distance/comfort_brake this LongitudinalMpc was constructed with."""
  if following_profile is None:
    return get_T_FOLLOW(personality), comfort_brake, stop_distance
  from opendbc.car.gm.volt_following import resolve_gap_params as volt_resolve_gap_params
  gap = volt_resolve_gap_params(personality, following_profile)
  return gap.t_follow, gap.comfort_brake, gap.stop_distance
