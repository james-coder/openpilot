from types import SimpleNamespace
import pytest

from openpilot.system import wind_monitor as wm


def test_should_fetch_first_time_then_interval_then_distance():
  assert wm.should_fetch(0., None, 0., 0., 40., -111.)
  assert not wm.should_fetch(60., 0., 40., -111., 40., -111.)                 # under MIN_INTERVAL_S
  assert not wm.should_fetch(300., 0., 40., -111., 40.01, -111.)              # moved ~0.7 mi, not due
  assert wm.should_fetch(wm.FETCH_INTERVAL_S, 0., 40., -111., 40., -111.)     # cadence
  assert wm.should_fetch(300., 0., 40., -111., 40.2, -111.)                   # ~14 mi north


def test_haversine_known_distance():
  assert wm.haversine_mi(40.7608, -111.8910, 40.2338, -111.6585) == pytest.approx(38, abs=2)  # SLC to Provo


def test_fix_from_rejects_bad_fixes():
  good = SimpleNamespace(hasFix=True, horizontalAccuracy=5., latitude=40.5, longitude=-111.9, bearingDeg=270.)
  assert wm.fix_from(good) == (40.5, -111.9, 270.)
  assert wm.fix_from(SimpleNamespace(**{**vars(good), 'hasFix': False})) is None
  assert wm.fix_from(SimpleNamespace(**{**vars(good), 'horizontalAccuracy': 5000.})) is None
  assert wm.fix_from(SimpleNamespace(**{**vars(good), 'latitude': 0., 'longitude': 0.})) is None
  assert wm.fix_from(None) is None


def test_fix_from_accepts_qcomgpsd_fix_without_accuracy():
  # What the comma 3X logged on 2026-09-25: hasFix set, flags and accuracy left at 0.
  qcom = SimpleNamespace(hasFix=True, flags=0, horizontalAccuracy=0., latitude=41.191, longitude=-111.97, bearingDeg=90.)
  assert wm.fix_from(qcom) == (41.191, -111.97, 90.)


def body(**over):
  d = {'current': {'time': '2026-09-21T15:00', 'interval': 900, 'wind_speed_10m': 12.3, 'wind_direction_10m': 315,
                   'wind_gusts_10m': 21.0}, 'current_units': {'wind_speed_10m': 'mph'}}
  d['current'].update(over)
  return d


def test_parse_response_ok_and_validation():
  r = wm.parse_response(body())
  assert r == {'wind_mph': 12.3, 'gust_mph': 21.0, 'dir_deg': 315, 'valid_time': '2026-09-21T15:00', 'interval_s': 900}
  assert wm.parse_response(body(wind_gusts_10m=None))['gust_mph'] is None
  with pytest.raises(ValueError):
    wm.parse_response(body(wind_speed_10m='12'))
  with pytest.raises(ValueError):
    wm.parse_response(body(wind_direction_10m=400))
  b = body()
  b['current_units']['wind_speed_10m'] = 'kmh'
  with pytest.raises(ValueError):
    wm.parse_response(b)
  with pytest.raises((ValueError, KeyError)):
    wm.parse_response({'current': {}})


def test_write_status_atomic(tmp_path):
  wm.write_status({'time': 1., 'state': 'ok'}, root=tmp_path / 'wind')
  assert (tmp_path / 'wind' / 'status.json').read_text() == '{"time": 1.0, "state": "ok"}'
  assert not (tmp_path / 'wind' / 'status.tmp').exists()


def test_load_previous_reading_survives_restart(tmp_path):
  assert wm.load_previous(tmp_path, 1000.) == (None, 0.)
  wm.write_status({'time': 990., 'state': 'ok', 'reading': wm.parse_response(body()), 'reading_time': 990.}, root=tmp_path)
  r, t = wm.load_previous(tmp_path, 1000.)
  assert (r['wind_mph'], r['dir_deg'], t) == (12.3, 315, 990.)
  wm.write_status({'time': 1., 'state': 'ok', 'reading': wm.parse_response(body()), 'reading_time': 1.}, root=tmp_path)
  assert wm.load_previous(tmp_path, 2 * 86400.) == (None, 0.)        # a day old: start fresh
  wm.write_status({'time': 990., 'state': 'ok', 'reading': {'wind_mph': 'x'}, 'reading_time': 990.}, root=tmp_path)
  assert wm.load_previous(tmp_path, 1000.) == (None, 0.)
