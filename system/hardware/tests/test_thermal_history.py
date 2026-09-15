import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.system.hardware.thermal_history import History, LIMIT, SENSORS, Timeline, fresh, read_summary, temperatures


def fill(history, end, value=60, start=0, mode='offroad'):
  for now in range(start, end+1, 5):
    history.sample(now, 1750000000+now, mode, dict.fromkeys(SENSORS, value), {'fan_percent': 20})
  return history.data['records'][f'{mode}/Memory']


def test_full_window_exposure_and_lifetime_maxima():
  history = History()
  record = fill(history, 595)
  assert record['avg600'] is None
  record = fill(history, 3600, start=600)
  assert record['seconds'] == 3600
  assert record['avg600']['value'] == record['avg3600']['value'] == 60
  assert record['above']['55'] == record['longest']['55'] == 3600
  assert record['above']['60'] == 0  # Strictly above, not greater-or-equal.
  fill(history, 7200, value=40, start=3605)
  assert record['peak']['value'] == 60
  assert record['avg3600']['value'] == 60
  assert len(history.windows['offroad/Memory']) <= 721


def test_time_weighted_not_sample_count_average():
  history = History()
  for now in range(0, 606, 5):
    history.sample(now, 1750000000+now, 'offroad', {'Memory': 50 if now <= 300 else 70}, {})
  assert history.data['records']['offroad/Memory']['avg600']['value'] == pytest.approx(60)


@pytest.mark.parametrize('kind', ['gap', 'missing', 'mode', 'restart'])
def test_no_window_or_streak_across_unknown_intervals(kind):
  history = History()
  fill(history, 595)
  if kind == 'gap':
    fill(history, 620, start=620)
  elif kind == 'missing':
    history.break_continuity()
    fill(history, 600, start=600)
  elif kind == 'mode':
    fill(history, 600, start=600, mode='onroad')
    fill(history, 605, start=605)
  else:
    history = History(history.data)
    fill(history, 600, start=600)
  record = history.data['records']['offroad/Memory']
  assert record['avg600'] is None
  assert record['seconds'] == 595
  assert record['longest']['55'] == 595


def test_missing_sensor_breaks_its_streak_and_invalid_samples_ignored():
  history = History()
  fill(history, 20)
  history.sample(25, 1750000025, 'offroad', {'Memory': float('nan'), 'CPU max': 900}, {})
  fill(history, 30, start=30)
  assert history.data['records']['offroad/Memory']['seconds'] == 20


def test_unsynchronized_dates_not_invented():
  history = History()
  history.sample(0, 0, 'offroad', {'Memory': 70}, {})
  assert history.data['since'] is None
  assert history.data['records']['offroad/Memory']['peak']['time'] is None


def test_save_reload_small_and_preserves_corruption(tmp_path):
  history = History()
  fill(history, 3600)
  history.save(tmp_path)
  before = (tmp_path / 'summary.json').read_bytes()
  assert len(before) < LIMIT
  restarted = History(read_summary(tmp_path))
  assert restarted.data['records']['offroad/Memory']['seconds'] == 3600
  assert restarted.previous == {}
  assert restarted.data['sessions'] == 2
  (tmp_path / 'summary.json').write_text('{broken')
  with pytest.raises(ValueError):
    read_summary(tmp_path)
  assert (tmp_path / 'summary.json').read_text() == '{broken'


def test_storage_limit_and_atomic_failure(tmp_path, monkeypatch):
  history = History()
  fill(history, 10)
  history.save(tmp_path)
  before = (tmp_path / 'summary.json').read_bytes()

  def denied(*args):
    raise PermissionError

  monkeypatch.setattr('os.replace', denied)
  for _ in range(3):
    with pytest.raises(PermissionError):
      history.save(tmp_path)
  assert (tmp_path / 'summary.json').read_bytes() == before
  assert len(list(tmp_path.iterdir())) == 2
  assert sum(p.stat().st_size for p in tmp_path.iterdir()) < 2*LIMIT


def test_oversized_or_invalid_summary_rejected(tmp_path):
  (tmp_path / 'summary.json').write_bytes(b' ' * (LIMIT+1))
  with pytest.raises(ValueError):
    read_summary(tmp_path)
  data = History().data
  data['records']['offroad/Memory']['seconds'] = -1
  (tmp_path / 'summary.json').write_text(json.dumps(data))
  with pytest.raises(ValueError):
    read_summary(tmp_path)


def test_no_screen_sensor_invented():
  ds = SimpleNamespace(cpuTempC=[50, 60], gpuTempC=[55], memoryTempC=56, pmicTempC=[58], modemTempC=[])
  assert temperatures(ds) == {'CPU max': 60, 'GPU max': 55, 'Memory': 56, 'PMIC max': 58, 'Modem max': None}


def test_freshness_requires_seen_valid_recent():
  sm = SimpleNamespace(seen={'x': True}, valid={'x': True}, recv_time={'x': 100})
  assert fresh(sm, 'x', 101)
  assert not fresh(sm, 'x', 102)
  assert not fresh(sm, 'x', 99)
  sm.valid['x'] = False
  assert not fresh(sm, 'x', 100)


def test_service_is_independent_and_unprivileged():
  from openpilot.system.manager.process_config import managed_processes
  assert 'thermal-history' not in managed_processes
  assert not any('thermal_history' in str(vars(p)) for p in managed_processes.values())
  from openpilot.system.hardware import thermal_history
  unit = Path(thermal_history.__file__).with_name('thermal-history.service').read_text()
  assert 'User=comma' in unit and 'CPUSchedulingPolicy=idle' in unit
  assert 'MemoryMax=64M' in unit
  assert 'Requires=' not in unit and 'WantedBy=multi-user.target' in unit


def test_timeline_bounded_and_retains_fan_power_context(tmp_path, monkeypatch):
  monkeypatch.setattr('openpilot.system.hardware.thermal_history.TIMELINE_ROWS', 10)
  timeline = Timeline(tmp_path)
  for i in range(25):
    timeline.add(1750000000+i*30, 'offroad', {'Memory': 75}, dict(fan_percent=40, fan_rpm=3000, watts=2, voltage=12.8))
  assert timeline.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 10
  row = timeline.db.execute('SELECT seq,memory,fan,rpm,watts,voltage FROM samples ORDER BY seq DESC LIMIT 1').fetchone()
  assert row == (24, 75, 40, 3000, 2, 12.8)
  assert timeline.db.execute('PRAGMA max_page_count').fetchone()[0] * timeline.db.execute('PRAGMA page_size').fetchone()[0] == 8*1024*1024
  timeline.db.close()
  restarted = Timeline(tmp_path)
  assert restarted.seq == 25
  restarted.add(0, 'offroad', {}, {})
  assert restarted.db.execute('SELECT t,rpm,memory FROM samples WHERE seq=25').fetchone() == (None, None, None)
  restarted.db.close()
