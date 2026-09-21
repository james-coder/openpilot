import json
import pytest

from openpilot.selfdrive.ui.onroad import wind_overlay as wo


def status(root, now=1000., status_age=0., reading_age=0., **over):
  reading = {'wind_mph': 12.3, 'gust_mph': 21., 'dir_deg': 315, **over}
  root.mkdir(exist_ok=True)
  (root / 'status.json').write_text(json.dumps({'time': now - status_age, 'state': 'ok', 'reading': reading,
                                                'reading_time': now - reading_age}))


def test_latest_wind_requires_live_daemon_and_fresh_reading(tmp_path):
  assert wo.latest_wind(tmp_path, 1000.) is None
  status(tmp_path)
  assert wo.latest_wind(tmp_path, 1000.) == (12.3, 21., 315., 0.)
  status(tmp_path, status_age=wo.STATUS_MAX_AGE_S + 1)
  assert wo.latest_wind(tmp_path, 1000.) is None                      # daemon dead
  status(tmp_path, reading_age=wo.READING_MAX_AGE_S + 1)
  assert wo.latest_wind(tmp_path, 1000.) is None                      # reading too old
  status(tmp_path, reading_age=900.)
  assert wo.latest_wind(tmp_path, 1000.)[3] == 900.                  # old but shown (dimmed by the renderer)
  status(tmp_path, reading_age=5 * 3600.)
  assert wo.latest_wind(tmp_path, 1000.)[3] == 5 * 3600.             # hours offline: still shown, with its age


def test_latest_wind_rejects_corrupt_data(tmp_path):
  status(tmp_path, wind_mph='fast')
  assert wo.latest_wind(tmp_path, 1000.) is None
  status(tmp_path, dir_deg=361)
  assert wo.latest_wind(tmp_path, 1000.) is None
  status(tmp_path, gust_mph='x')
  assert wo.latest_wind(tmp_path, 1000.)[1] is None                  # bad gust just drops the gust
  (tmp_path / 'status.json').write_text('{nope')
  assert wo.latest_wind(tmp_path, 1000.) is None
  (tmp_path / 'status.json').write_text(json.dumps({'time': 1000., 'state': 'nofix', 'reading': None, 'reading_time': 0}))
  assert wo.latest_wind(tmp_path, 1000.) is None


def test_compass_and_relative_angle():
  assert [wo.compass(d) for d in (0, 22, 23, 90, 180, 270, 359)] == ['N', 'N', 'NE', 'E', 'S', 'W', 'N']
  # wind FROM the north while driving north: it blows toward the south = headwind = 180 on screen
  assert wo.relative_to_heading(0., 0.) == 180.
  # wind FROM the south while driving north: tailwind = 0
  assert wo.relative_to_heading(180., 0.) == 0.
  # wind FROM the west while driving north: blows east = to the car's right = 90
  assert wo.relative_to_heading(270., 0.) == 90.
  # same wind, driving east: blows straight ahead = tailwind
  assert wo.relative_to_heading(270., 90.) == 0.
  assert wo.relative_to_heading(45., 300.) == pytest.approx(285.)
