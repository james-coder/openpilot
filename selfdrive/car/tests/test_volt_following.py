import json

from opendbc.car import structs
from opendbc.car.gm.values import CAR

from openpilot.selfdrive.car.volt_following import following_gap_kwargs, load_following_profile, parse_following_profile


def make_cp(fingerprint=CAR.CHEVROLET_VOLT):
  return structs.CarParams.new_message(carFingerprint=fingerprint, openpilotLongitudinalControl=True)


VALID_JSON = json.dumps({
  'version': 'volt-following-v1', 'validated': True, 'fit_time': '2026-09-17T00:00:00Z', 'heldout_rmse_m': 0.4,
  # comfort_brake descends aggressive->relaxed on purpose (it's a divisor: higher = shorter gap).
  'aggressive': {'t_follow': 1.0, 'comfort_brake': 2.5, 'stop_distance': 5.5},
  'standard': {'t_follow': 1.2, 'comfort_brake': 2.2, 'stop_distance': 6.0},
  'relaxed': {'t_follow': 1.6, 'comfort_brake': 2.0, 'stop_distance': 6.5},
})


def test_parse_following_profile_rejects_missing_and_malformed():
  assert parse_following_profile('') is None
  assert parse_following_profile(None) is None
  assert parse_following_profile('{not json') is None
  assert parse_following_profile(json.dumps({'version': 'x'})) is None  # missing required fields


def test_parse_following_profile_rejects_invalid_bounds():
  broken = json.loads(VALID_JSON)
  broken['aggressive']['stop_distance'] = 100.0
  assert parse_following_profile(json.dumps(broken)) is None


def test_parse_following_profile_accepts_valid():
  profile = parse_following_profile(VALID_JSON)
  assert profile is not None
  assert profile.validated
  assert profile.aggressive.stop_distance == 5.5


def test_load_following_profile_falls_back_to_stock_default_when_file_missing(tmp_path):
  profile = load_following_profile(tmp_path / 'does-not-exist.json')
  assert not profile.validated


def test_load_following_profile_falls_back_on_invalid_file_contents(tmp_path):
  broken = json.loads(VALID_JSON)
  broken['aggressive']['stop_distance'] = 100.0
  path = tmp_path / 'profile.json'
  path.write_text(json.dumps(broken))
  profile = load_following_profile(path)
  assert not profile.validated


def test_load_following_profile_reads_valid_file(tmp_path):
  path = tmp_path / 'profile.json'
  path.write_text(VALID_JSON)
  profile = load_following_profile(path)
  assert profile.validated
  assert profile.aggressive.stop_distance == 5.5


def test_following_gap_kwargs_empty_without_valid_profile_file(tmp_path):
  assert following_gap_kwargs(make_cp(), tmp_path / 'missing.json') == {}


def test_following_gap_kwargs_empty_for_non_volt_even_with_valid_profile(tmp_path):
  path = tmp_path / 'profile.json'
  path.write_text(VALID_JSON)
  assert following_gap_kwargs(make_cp(CAR.CHEVROLET_BOLT_EUV), path) == {}


def test_following_gap_kwargs_populated_for_volt_with_valid_profile(tmp_path):
  path = tmp_path / 'profile.json'
  path.write_text(VALID_JSON)
  kwargs = following_gap_kwargs(make_cp(), path)
  assert set(kwargs) == {'following_profile'}
  assert kwargs['following_profile'].aggressive.stop_distance == 5.5
