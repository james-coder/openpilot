from copy import deepcopy

import numpy as np
import pytest

from openpilot.tools.profiling.volt_response_fit import fit_stopping_curve
from openpilot.tools.profiling.volt_braking import describe_event
from openpilot.tools.profiling.validate_volt_braking import matches_manual_finish
from openpilot.selfdrive.test.longitudinal_maneuvers.volt_plant import metrics


def test_holdout_does_not_change_fitted_taper():
  examples = [{'id': 'train', 'route': 'a'}, {'id': 'test', 'route': 'b'}]
  rows = [{'v': float(v), 'a': -0.6 - float(v) * 0.2} for v in np.linspace(0.3, 2, 200)]
  samples = {'train': rows, 'test': deepcopy(rows)}
  original = fit_stopping_curve(examples, samples)
  for row in samples['test']:
    row['a'] -= 1
  changed = fit_stopping_curve(examples, samples)
  assert original['stop_decel'] == changed['stop_decel']
  assert original['stop_speed'] == changed['stop_speed']
  assert changed['holdout_rmse'] > original['holdout_rmse'] + 0.8
  assert not changed['validated'] and not changed['runtime_applied']
  assert fit_stopping_curve(examples[:1], samples) is None


def test_smoother_crawl_does_not_satisfy_manual_finishing_target():
  reference = [{'low_speed_seconds': 1.5, 'final_jerk_p95': 1.5}, {'low_speed_seconds': 1.8, 'final_jerk_p95': 1.2}]
  result = {'stopped': True, 'low_speed_seconds': 1.7, 'final_jerk_p95': 0.5, 'speed_rebound': 0}
  assert matches_manual_finish(result, reference)
  assert not matches_manual_finish({**result, 'low_speed_seconds': 4}, reference)
  assert not matches_manual_finish({**result, 'final_jerk_p95': 3}, reference)
  assert not matches_manual_finish({**result, 'speed_rebound': 0.1}, reference)
  assert not matches_manual_finish({**result, 'stopped': False}, reference)
  assert not matches_manual_finish(result, [])


def test_simulated_finish_metrics_match_native_review_windows():
  rows = []
  for t in np.arange(0, 8, 0.01):
    rows.append({
      't': float(t), 'v': max(0.0, 3.0 - max(0.0, float(t) - 2)),
      'a': float(np.sin(t * 3)), 'foot': False, 'regen': False, 'active': True,
    })
  # An earlier low-speed stretch must not inflate the final contiguous phase.
  for row in rows[:100]:
    row['v'] = 0.6
  stop = next(i for i, r in enumerate(rows) if r['v'] < 0.3)
  native = describe_event([{**r, 't': r['t'] - rows[stop]['t']} for r in rows])
  simulated = metrics(rows)
  assert simulated['low_speed_seconds'] == pytest.approx(native['low_speed_seconds'])
  assert simulated['final_jerk_p95'] == pytest.approx(native['final_jerk_p95'], rel=0.01)
