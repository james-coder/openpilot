from cereal import car

from openpilot.selfdrive.car.card import carcontrol_fresh, radar_tracks_valid


def errors(**kw):
  return car.RadarData.Error.new_message(**kw)


def test_radar_degraded_keeps_tracks_valid():
  assert radar_tracks_valid(errors())
  assert radar_tracks_valid(errors(radarDegraded=True, radarDegradedReasons=8))


def test_real_radar_errors_still_invalidate_tracks():
  assert not radar_tracks_valid(errors(canError=True))
  assert not radar_tracks_valid(errors(radarDegraded=True, radarUnavailableTemporary=True))


def test_carcontrol_freshness_is_age_only():
  assert carcontrol_fresh(1_000_000_000, 1_000_000_000 - 100_000_000)
  assert not carcontrol_fresh(1_000_000_000, 1_000_000_000 - 151_000_000)
  assert not carcontrol_fresh(1_000_000_000, 1_000_000_001)  # from the future
