from datetime import datetime

from openpilot.selfdrive.ui.onroad.check_engine_overlay import badge_text

TIMESTAMP = '2026-09-12T07:09:52+00:00'
SCANNED = datetime.fromisoformat(TIMESTAMP).timestamp()


def report(mil, codes=('P0401',)):
  return {'state': 'complete', 'timestamp': TIMESTAMP, 'ecus': {
    '7E8': {'lamp': {'state': 'ok', 'mil': mil}, 'stored': {'state': 'ok', 'codes': list(codes)}},
    '7E9': {'lamp': {'state': 'ok', 'mil': False}}}}


def test_badge_follows_the_lamp_not_the_codes():
  assert badge_text(report(True), SCANNED + 60) == 'CHECK ENGINE P0401'
  assert badge_text(report(False), SCANNED + 60) is None  # light out, P0401 still on file
  assert badge_text(None, SCANNED) is None
  assert badge_text({'state': 'complete', 'ecus': {'7E8': {'stored': {'state': 'ok', 'codes': ['P0401']}}}}, SCANNED) is None


def test_badge_truncates_codes_and_shows_age_of_old_scan():
  assert badge_text(report(True, ['P0101', 'P0300', 'P0401', 'P0420']), SCANNED) == 'CHECK ENGINE P0101 P0300 P0401 +1'
  assert badge_text(report(True, []), SCANNED) == 'CHECK ENGINE'
  assert badge_text(report(True), SCANNED + 2 * 86400) == 'CHECK ENGINE P0401 (2d old)'


def test_badge_never_raises_on_malformed_param():
  for value in (5, 'x', [1], {'state': 'complete', 'ecus': 'bad'}, {'state': 'complete', 'ecus': {'7E8': {'lamp': 'bad'}}}):
    assert badge_text(value, SCANNED) is None
