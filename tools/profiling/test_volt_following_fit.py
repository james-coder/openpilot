import numpy as np
import pytest

from cereal import log
from opendbc.car.gm.volt_following import GapParams
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.gap_params import COMFORT_BRAKE, get_T_FOLLOW, get_safe_obstacle_distance
from openpilot.tools.profiling.volt_following_fit import (
  AGGRESSIVE_PADDING, MIN_SAMPLES_PER_BIN, bin_samples, evaluate_holdout, fit, fit_curve, midpoint_gap, pad_gap,
)


def synthetic_samples(gap: GapParams, route, n_per_speed=40, speeds=(2., 5., 10., 20., 28.), noise=0.05, seed=0):
  rng = np.random.default_rng(seed)
  samples = []
  for v in speeds:
    center = get_safe_obstacle_distance(v, gap.t_follow, gap.stop_distance, gap.comfort_brake)
    for _ in range(n_per_speed):
      samples.append({'v': float(v + rng.normal(0, 0.05)), 'd': float(center + rng.normal(0, noise)), 't': 0.0, 'route': route})
  return samples


TRUE_MIN = GapParams(t_follow=1.15, comfort_brake=COMFORT_BRAKE, stop_distance=4.7)
TRUE_MAX = GapParams(t_follow=1.6, comfort_brake=COMFORT_BRAKE, stop_distance=7.0)


def test_bin_samples_flags_low_confidence_bins():
  samples = [{'v': 5.5, 'd': 10.0} for _ in range(MIN_SAMPLES_PER_BIN - 1)]  # 5..7 bin, below threshold
  bins = bin_samples(samples)
  bin_5_7 = next(b for b in bins if b['speed_lo'] == 5)
  assert bin_5_7['low_confidence']
  assert bin_5_7['count'] == MIN_SAMPLES_PER_BIN - 1


def test_bin_samples_computes_min_max_percentiles_when_well_supported():
  samples = [{'v': 5.5, 'd': float(d)} for d in range(1, MIN_SAMPLES_PER_BIN + 20)]
  bins = bin_samples(samples)
  bin_5_7 = next(b for b in bins if b['speed_lo'] == 5)
  assert not bin_5_7['low_confidence']
  assert bin_5_7['min'] == 1.0
  assert bin_5_7['max'] == float(MIN_SAMPLES_PER_BIN + 19)


def test_fit_curve_recovers_known_ground_truth_parameters():
  samples = synthetic_samples(TRUE_MIN, 'r1', noise=0.02)
  bins = bin_samples(samples)
  for b in bins:
    if not b['low_confidence']:
      b['min'] = b['median']  # synthetic samples don't scatter enough for real min/max; fit against a clean center
  fitted = fit_curve(bins, 'min', get_T_FOLLOW(log.LongitudinalPersonality.aggressive))
  assert fitted.t_follow == pytest.approx(TRUE_MIN.t_follow, abs=0.1)
  assert fitted.comfort_brake == COMFORT_BRAKE  # held fixed, not fit
  assert fitted.stop_distance == pytest.approx(TRUE_MIN.stop_distance, abs=0.3)


def test_fit_curve_raises_with_too_few_supported_bins():
  samples = [{'v': 5.5, 'd': 10.0} for _ in range(5)]  # far below MIN_SAMPLES_PER_BIN
  bins = bin_samples(samples)
  with pytest.raises(ValueError):
    fit_curve(bins, 'min', 1.25)


def test_pad_gap_scales_time_and_distance_not_comfort_brake():
  gap = GapParams(1.0, 2.0, 5.0)
  padded = pad_gap(gap, AGGRESSIVE_PADDING)
  assert padded.t_follow == pytest.approx(1.05)
  assert padded.stop_distance == pytest.approx(5.25)
  assert padded.comfort_brake == 2.0


def test_midpoint_gap_averages_each_parameter_independently():
  a, b = GapParams(1.0, 2.0, 5.0), GapParams(2.0, 3.0, 7.0)
  mid = midpoint_gap(a, b)
  assert (mid.t_follow, mid.comfort_brake, mid.stop_distance) == (1.5, 2.5, 6.0)


def test_evaluate_holdout_returns_none_for_empty_and_rmse_otherwise():
  gap = GapParams(1.25, 2.5, 6.0)
  assert evaluate_holdout(gap, []) is None
  samples = [{'v': 10.0, 'd': get_safe_obstacle_distance(10.0, 1.25, 6.0, 2.5)}]
  assert evaluate_holdout(gap, samples) == pytest.approx(0.0, abs=1e-9)


def test_fit_requires_at_least_two_routes():
  samples = synthetic_samples(TRUE_MIN, 'only-route')
  with pytest.raises(ValueError):
    fit(samples)


def test_fit_end_to_end_monotone_and_checks_reported():
  train_min = synthetic_samples(TRUE_MIN, 'train', speeds=(2., 5., 10., 20., 28.), noise=0.03, seed=1)
  train_max = synthetic_samples(TRUE_MAX, 'train', speeds=(2., 5., 10., 20., 28.), noise=0.03, seed=2)
  holdout = synthetic_samples(midpoint_gap(TRUE_MIN, TRUE_MAX), 'holdout', noise=0.03, seed=3)
  report, profile = fit(train_min + train_max + holdout, holdout_route='holdout')
  assert report['holdout_route'] == 'holdout'
  assert report['aggressive']['t_follow'] <= report['standard']['t_follow'] <= report['relaxed']['t_follow']
  assert report['aggressive']['stop_distance'] <= report['standard']['stop_distance'] <= report['relaxed']['stop_distance']
  assert 'checks_passed' in report
  assert not profile.validated  # the report/profile from fit() alone is always advisory
