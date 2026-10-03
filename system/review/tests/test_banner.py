import json

from openpilot.system.review.banner import SHOW_SECONDS, banner_text

BOOT = 'abc-123'


def record(**kw):
  r = dict(kinds=['hard_brake'], boot_id=BOOT, written_boot_s=1000.0)
  r.update(kw)
  return r


def test_banner_shows_recent_events_in_priority_order():
  assert banner_text(record(), BOOT, 1002.0) == 'EVENT SAVED: HARD BRAKE'
  text = banner_text(record(kinds=['impact', 'extreme_g', 'hard_brake', 'abs_stop']), BOOT, 1000.0)
  assert text == 'EVENT SAVED: ABS STOP, HARD BRAKE, EXTREME G-FORCE, POSSIBLE IMPACT'


def test_banner_expires_and_never_shows_stale_or_foreign_records():
  assert banner_text(record(), BOOT, 1000.0 + SHOW_SECONDS + 0.1) is None
  assert banner_text(record(), BOOT, 999.0) is None                 # written "in the future": clock went backwards
  assert banner_text(record(boot_id='other-boot'), BOOT, 1001.0) is None  # left over from an earlier boot
  assert banner_text(record(), '', 1001.0) is None                  # unknown current boot: show nothing


def test_malformed_records_show_nothing_and_never_raise():
  for bad in (None, 5, 'x', [], {}, record(kinds=[]), record(kinds=['unknown']), record(kinds=5), record(written_boot_s='soon'),
              record(written_boot_s=None), json.loads('{"boot_id": "abc-123"}')):
    assert banner_text(bad, BOOT, 1001.0) is None
