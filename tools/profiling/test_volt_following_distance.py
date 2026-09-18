from datetime import datetime, UTC
import os

import numpy as np
import pytest

from openpilot.tools.profiling.volt_following_distance import STEADY_WINDOW_S, extract_routes, route_age_days, steady_windows


def make_rows(n=120, dt=0.05, v=10.0, d=20.0, vrel=0.0, enabled=False, status=True, track=1, prob=0.95,
              standstill=False, d_noise=0.0, seed=0):
  rng = np.random.default_rng(seed)
  rows = []
  for i in range(n):
    rows.append({
      't': i * dt,
      'v': v,
      'standstill': standstill,
      'selfdriveState': {'enabled': enabled},
      'radarState': {'status': status, 'dRel': d + (rng.normal(0, d_noise) if d_noise else 0.0),
                     'vRel': vrel, 'radarTrackId': track, 'modelProb': prob},
    })
  return rows


def test_clean_steady_window_yields_samples():
  rows = make_rows(n=120, v=10.0, d=20.0)
  samples = list(steady_windows(rows))
  assert len(samples) > 0
  v, d, t = samples[-1]
  assert v == pytest.approx(10.0)
  assert d == pytest.approx(20.0)


def test_first_samples_before_window_fills_are_excluded():
  rows = make_rows(n=120)
  samples = list(steady_windows(rows))
  first_t = samples[0][2]
  assert first_t >= STEADY_WINDOW_S - 1e-6


def test_engaged_openpilot_excludes_window():
  rows = make_rows(n=120, enabled=True)
  assert list(steady_windows(rows)) == []


def test_standstill_excludes_window():
  rows = make_rows(n=120, standstill=True)
  assert list(steady_windows(rows)) == []


def test_below_min_speed_excludes_window():
  rows = make_rows(n=120, v=0.5)
  assert list(steady_windows(rows)) == []


def test_no_lead_excludes_window():
  rows = make_rows(n=120, status=False)
  assert list(steady_windows(rows)) == []


def test_low_model_prob_excludes_window():
  rows = make_rows(n=120, prob=0.5)
  assert list(steady_windows(rows)) == []


def test_track_handoff_excludes_the_window_it_falls_in():
  rows = make_rows(n=300, track=1)  # 15s total, plenty of room after the handoff for a full window
  for r in rows[100:]:
    r['radarState']['radarTrackId'] = 2
  samples = list(steady_windows(rows))
  # No window spanning the handoff should qualify; later windows (fully on track 2,
  # once the window has refilled) should.
  handoff_t = rows[100]['t']
  # A window ending at t covers [t-W, t]; it's mixed-track (invalid) exactly when
  # handoff_t < t < handoff_t + W. Pure track1 (t <= handoff_t) and pure track2
  # (t >= handoff_t + W) are both fine and expected.
  assert not any(handoff_t < t < handoff_t + STEADY_WINDOW_S for _, _, t in samples)
  assert any(t >= handoff_t + STEADY_WINDOW_S for _, _, t in samples)


def test_large_relative_velocity_excludes_window():
  rows = make_rows(n=120, vrel=3.0)
  assert list(steady_windows(rows)) == []


def test_wobbly_but_proportionally_small_gap_at_low_speed_is_still_steady():
  # Absolute std small (0.3m) but the gap itself is small (4m) -> relative std is fine.
  rows = make_rows(n=120, v=2.0, d=4.0, d_noise=0.15, seed=1)
  samples = list(steady_windows(rows))
  assert len(samples) > 0


def test_large_relative_gap_wobble_excludes_window():
  rows = make_rows(n=120, v=2.0, d=4.0, d_noise=1.0, seed=2)
  assert list(steady_windows(rows)) == []


def test_route_age_days_uses_file_mtime(tmp_path):
  # Real on-device route dirs (e.g. 00000087--98ef106935--14) carry no parseable
  # timestamp at all, so age must come from the rlog file's own mtime.
  rlog = tmp_path / 'rlog.zst'
  rlog.write_bytes(b'x')
  seven_days_ago = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
  os.utime(rlog, (seven_days_ago.timestamp(), seven_days_ago.timestamp()))
  now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
  assert route_age_days(rlog, now) == pytest.approx(7.0, abs=1e-6)


def test_route_age_days_returns_none_for_missing_file(tmp_path):
  assert route_age_days(tmp_path / 'does-not-exist.zst') is None


def test_one_corrupt_segment_does_not_crash_the_whole_extraction(tmp_path):
  """Regression test: a real malformed rlog once raised an exception from inside the
  capnp C extension that ProcessPoolExecutor couldn't pickle, crashing every other
  in-flight segment along with it. A plain garbage file reproduces the same shape of
  failure (extract_native raises) without needing a real corrupt capnp/zstd fixture."""
  good_dir = tmp_path / 'raw' / 'good--route--0'
  good_dir.mkdir(parents=True)
  # Not a real rlog (would need genuine zstd+capnp encoding); this only needs to prove
  # the pool survives one segment raising and still returns cleanly with an error entry.
  (good_dir / 'rlog.zst').write_bytes(b'not a real rlog')
  bad_dir = tmp_path / 'raw' / 'bad--route--0'
  bad_dir.mkdir(parents=True)
  (bad_dir / 'rlog.zst').write_bytes(b'also not a real rlog')

  samples, skipped_old, errors = extract_routes(tmp_path / 'raw', tmp_path / 'cache')
  assert samples == []
  assert skipped_old == 0
  assert {e['segment'] for e in errors} == {'good--route--0', 'bad--route--0'}
  assert all(e['error'] for e in errors)  # non-empty message, not a bare crash
