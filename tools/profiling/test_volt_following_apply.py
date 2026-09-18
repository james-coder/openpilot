from opendbc.car.gm.volt_following import following_profile_valid
from openpilot.tools.profiling.volt_following_apply import profile_from_report


VALID_REPORT = {
  'fit_time': '2026-09-17T00:00:00Z', 'heldout_rmse_m': 0.4,
  # comfort_brake descends aggressive->relaxed on purpose (it's a divisor: higher = shorter gap).
  'aggressive': {'t_follow': 1.0, 'comfort_brake': 2.5, 'stop_distance': 5.5},
  'standard': {'t_follow': 1.2, 'comfort_brake': 2.2, 'stop_distance': 6.0},
  'relaxed': {'t_follow': 1.6, 'comfort_brake': 2.0, 'stop_distance': 6.5},
  'checks_passed': True,
}


def test_profile_from_report_round_trips_and_validates():
  profile = profile_from_report(VALID_REPORT)
  assert profile.validated
  assert profile.aggressive.stop_distance == 5.5
  assert following_profile_valid(profile)


def test_profile_from_report_rejects_out_of_bounds():
  broken = {**VALID_REPORT, 'aggressive': {**VALID_REPORT['aggressive'], 'stop_distance': 100.0}}
  profile = profile_from_report(broken)
  assert not following_profile_valid(profile)  # this is what main() checks before writing Params
